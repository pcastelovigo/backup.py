VERSION = 2
import os
import subprocess
from datetime import datetime
from pathlib import Path
import argparse

import yaml
import boto3


class ConfigError(Exception):
    pass


def load_config(file_path: Path):
    _, ext = os.path.splitext(file_path)
    if ext not in [".yaml", ".yml"]:
        raise ConfigError(f"Unsupported file extension: {ext}")
    with open(file_path, "rb") as f:
        return yaml.safe_load(f)


def get_config_path():
    parser = argparse.ArgumentParser(description="Backup script with YAML config.")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML configuration file",
        default=Path(__file__).resolve().parent / "config.yaml",
    )
    args = parser.parse_args()
    return args.config


def log(message):
    print(message)


class Compressor:
    def compress(self, file_path: Path, method: str):
        try:
            if method == "gzip":
                subprocess.run(["gzip", str(file_path)], check=True)
                new_path = file_path.with_suffix(file_path.suffix + ".gz")
                log(f"COMPRESSED {file_path.name} -> {new_path.name}")
                return new_path

            if method == "bzip2":
                subprocess.run(["bzip2", str(file_path)], check=True)
                new_path = file_path.with_suffix(file_path.suffix + ".bz2")
                log(f"COMPRESSED {file_path.name} -> {new_path.name}")
                return new_path

            log(f"ERROR, not supported: {method}")
            return None

        except subprocess.CalledProcessError as exc:
            log(f"ERROR, compressing file {file_path.name}: {exc}")
            return None


class Encryptor:
    def encrypt(self, file_path: Path, encryption):
        try:
            if encryption.get("method") == "gpg":
                encrypted_path = file_path.with_suffix(file_path.suffix + ".gpg")
                subprocess.run(
                    [
                        "gpg",
                        "--batch",
                        "--yes",
                        "--output",
                        str(encrypted_path),
                        "--encrypt",
                        "--recipient",
                        encryption["recipient"],
                        str(file_path),
                    ],
                    check=True,
                )
                log(f"ENCRYPTED {file_path.name} -> {encrypted_path.name}")
                return encrypted_path

            log(f"ERROR, not supported: {encryption.get('method')}")
            return None

        except subprocess.CalledProcessError as exc:
            log(f"ERROR encrypting file {file_path.name}: {exc}")
            return None


class Uploader:
    def upload(self, file_path: Path, destination):
        try:
            if destination.get("method") == "s3":
                client_kwargs = {}
                if destination.get("AWS_ACCESS_KEY_ID"):
                    client_kwargs["aws_access_key_id"] = destination["AWS_ACCESS_KEY_ID"]
                if destination.get("AWS_SECRET_ACCESS_KEY"):
                    client_kwargs["aws_secret_access_key"] = destination["AWS_SECRET_ACCESS_KEY"]
                if destination.get("AWS_SESSION_TOKEN"):
                    client_kwargs["aws_session_token"] = destination["AWS_SESSION_TOKEN"]

                s3 = boto3.client("s3", **client_kwargs)
                bucket = destination["S3_BUCKET"]
                prefix = destination.get("prefix", "").strip("/")
                key = f"{prefix}/{file_path.name}" if prefix else file_path.name

                s3.upload_file(str(file_path), bucket, key)
                log(f"UPLOADED {file_path.name} -> s3://{bucket}/{key}")
                return True

            log(f"ERROR, Not supported: {destination.get('method')}")
            return False

        except Exception as exc:
            log(f"ERROR uploading: {exc}")
            return False


class BackupTask:
    def run(self):
        raise NotImplementedError


class MySQLDumpTask(BackupTask):
    def __init__(self, source_config, destinations, encryptions, compressor, encryptor, uploader):
        self.source_config = source_config
        self.destinations = destinations or {}
        self.encryptions = encryptions or {}
        self.compressor = compressor
        self.encryptor = encryptor
        self.uploader = uploader
        self.system_databases = ["mysql", "information_schema", "performance_schema", "sys"]

    def run(self):
        for name in self.source_config:
            cfg = self.source_config[name]
            temp_dir = Path(cfg.get("temp", ""))
            if not str(temp_dir):
                log(f"ERROR, missing temp path for source {name}")
                continue
            temp_dir.mkdir(parents=True, exist_ok=True)

            all_except_system = cfg.get("all_databases_except_system", False)
            databases = cfg.get("databases") or []
            if all_except_system:
                exclude = cfg.get("exclude_databases") or self.system_databases
                databases = self._list_databases(cfg, exclude)
                if not databases:
                    log(f"ERROR, no databases found for source {name}")
                    continue
            elif not databases:
                log(f"ERROR, no databases listed for source {name}")
                continue

            compress_method = cfg.get("compress")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")

            for db in databases:
                dump_file = temp_dir / f"{db}_{timestamp}.sql"
                cmd = self._mysqldump_cmd(cfg, db)
                env = self._mysql_env(cfg)

                with open(dump_file, "wb") as f:
                    result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)

                if result.returncode != 0:
                    log(f"Error dumping {db}: {result.stderr.decode(errors='replace')}")
                    continue

                log(f"DUMPED {db} -> {dump_file}")

                if compress_method:
                    compressed_file = self.compressor.compress(dump_file, compress_method)
                    if not compressed_file:
                        continue
                    dump_file = compressed_file

                encryption_key = cfg.get("encryption")
                if encryption_key:
                    encryption_cfg = self.encryptions.get(encryption_key)
                    if not encryption_cfg:
                        log(f"ERROR, encryption not found: {encryption_key}")
                        continue
                    encrypted_file = self.encryptor.encrypt(dump_file, encryption_cfg)
                    if not encrypted_file:
                        continue
                    if dump_file.exists():
                        dump_file.unlink()
                    dump_file = encrypted_file

                destination_key = cfg.get("destination")
                if destination_key:
                    destination_cfg = self.destinations.get(destination_key)
                    if not destination_cfg:
                        log(f"ERROR, destination not found: {destination_key}")
                        continue
                    uploaded = self.uploader.upload(dump_file, destination_cfg)
                    if uploaded and cfg.get("cleanup", True):
                        dump_file.unlink()
                else:
                    log(f"INFO, no destination configured for {dump_file.name}")

    def _mysql_args(self, cfg):
        args = []
        if cfg.get("host"):
            args.extend(["-h", str(cfg["host"])])
        if cfg.get("port"):
            args.extend(["-P", str(cfg["port"])])
        if cfg.get("user"):
            args.extend(["-u", str(cfg["user"])])
        if cfg.get("extra_args"):
            args.extend(cfg["extra_args"])
        return args

    def _mysqldump_cmd(self, cfg, db):
        args = ["mysqldump"] + self._mysql_args(cfg)
        return args + [db]

    def _list_databases(self, cfg, exclude):
        cmd = ["mysql"] + self._mysql_args(cfg) + ["-N", "-e", "SHOW DATABASES"]
        env = self._mysql_env(cfg)
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        if result.returncode != 0:
            log(f"ERROR listing databases: {result.stderr.decode(errors='replace')}")
            return []
        exclude_set = set(exclude)
        databases = []
        for line in result.stdout.decode(errors="replace").splitlines():
            name = line.strip()
            if not name or name in exclude_set:
                continue
            databases.append(name)
        return databases

    def _mysql_env(self, cfg):
        env = os.environ.copy()
        if cfg.get("password"):
            env["MYSQL_PWD"] = str(cfg["password"])
        return env


class DirectoryBackupTask(BackupTask):
    def __init__(self, source_config, destinations, encryptions, compressor, encryptor, uploader):
        self.source_config = source_config
        self.destinations = destinations or {}
        self.encryptions = encryptions or {}
        self.compressor = compressor
        self.encryptor = encryptor
        self.uploader = uploader

    def run(self):
        for name in self.source_config:
            cfg = self.source_config[name]
            source_dir = Path(cfg.get("path", ""))
            if not source_dir.is_dir():
                log(f"ERROR, invalid source path for {name}: {source_dir}")
                continue

            temp_dir = Path(cfg.get("temp", ""))
            if not str(temp_dir):
                log(f"ERROR, missing temp path for source {name}")
                continue
            temp_dir.mkdir(parents=True, exist_ok=True)

            compress_method = cfg.get("compress")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            archive_name = self._archive_name(source_dir.name, timestamp, compress_method)
            archive_path = temp_dir / archive_name

            cmd = self._tar_cmd(cfg, source_dir, archive_path, compress_method)
            if not cmd:
                continue
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                log(f"ERROR creating archive {archive_name}: {result.stderr.decode(errors='replace')}")
                continue

            log(f"ARCHIVED {source_dir} -> {archive_path}")

            encryption_key = cfg.get("encryption")
            if encryption_key:
                encryption_cfg = self.encryptions.get(encryption_key)
                if not encryption_cfg:
                    log(f"ERROR, encryption not found: {encryption_key}")
                    continue
                encrypted_file = self.encryptor.encrypt(archive_path, encryption_cfg)
                if not encrypted_file:
                    continue
                if archive_path.exists():
                    archive_path.unlink()
                archive_path = encrypted_file

            destination_key = cfg.get("destination")
            if destination_key:
                destination_cfg = self.destinations.get(destination_key)
                if not destination_cfg:
                    log(f"ERROR, destination not found: {destination_key}")
                    continue
                uploaded = self.uploader.upload(archive_path, destination_cfg)
                if uploaded and cfg.get("cleanup", True):
                    archive_path.unlink()
            else:
                log(f"INFO, no destination configured for {archive_path.name}")

    def _tar_cmd(self, cfg, source_dir, archive_path, compress_method):
        args = ["tar"]
        if compress_method == "gzip":
            args.append("-czf")
        elif compress_method == "bzip2":
            args.append("-cjf")
        else:
            args.append("-cf")

        args.append(str(archive_path))

        if cfg.get("incremental"):
            snapshot = cfg.get("incremental_snapshot")
            if not snapshot:
                log("ERROR, incremental requires incremental_snapshot")
                return None
            snapshot_path = Path(snapshot)
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            args.extend(["--listed-incremental", str(snapshot_path)])

        args.extend(["-C", str(source_dir.parent), str(source_dir.name)])
        return args

    def _archive_name(self, base_name, timestamp, compress_method):
        if compress_method == "gzip":
            return f"{base_name}_{timestamp}.tar.gz"
        if compress_method == "bzip2":
            return f"{base_name}_{timestamp}.tar.bz2"
        return f"{base_name}_{timestamp}.tar"


class BackupRunner:
    def __init__(self, config):
        self.config = config
        self.compressor = Compressor()
        self.encryptor = Encryptor()
        self.uploader = Uploader()

    def build_tasks(self):
        tasks = []
        sources = self.config.get("sources") or {}
        destinations = self.config.get("destinations")
        encryptions = self.config.get("encryptions")

        for source_type in sources:
            if source_type == "mysqldump":
                tasks.append(
                    MySQLDumpTask(
                        sources[source_type],
                        destinations,
                        encryptions,
                        self.compressor,
                        self.encryptor,
                        self.uploader,
                    )
                )
            elif source_type == "directories":
                tasks.append(
                    DirectoryBackupTask(
                        sources[source_type],
                        destinations,
                        encryptions,
                        self.compressor,
                        self.encryptor,
                        self.uploader,
                    )
                )
            else:
                log(f"ERROR, source not supported yet: {source_type}")
        return tasks

    def run(self):
        tasks = self.build_tasks()
        for task in tasks:
            task.run()


if __name__ == "__main__":
    config_path = get_config_path()
    config = load_config(config_path)
    runner = BackupRunner(config)
    runner.run()
