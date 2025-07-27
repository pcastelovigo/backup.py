VERSION = 1
import os
import subprocess
from datetime import datetime
from pathlib import Path
import argparse

import yaml
import boto3

def load_config(file_path):
    _, ext = os.path.splitext(file_path)
    with open(file_path, 'rb') as f:
        if ext in ['.yaml', '.yml']:
            return yaml.safe_load(f)
        else:
            raise ValueError(f"Unsupported file extension: {ext}")


def get_config_path():
    parser = argparse.ArgumentParser(description="Backup script with YAML config.")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML configuration file",
        default=Path(__file__).resolve().parent / "config.yaml"
    )
    args = parser.parse_args()
    return args.config

########################### COMPRESS

def compress_file(file_path: Path, method: str):
    try:
        if method == 'gzip':
            subprocess.run(['gzip', str(file_path)], check=True)
            new_path = file_path.with_suffix(file_path.suffix + '.gz')
            print(f"COMPRESSED {file_path.name} → {new_path.name}")
            return new_path

        elif method == 'bzip2':
            subprocess.run(['bzip2', str(file_path)], check=True)
            new_path = file_path.with_suffix(file_path.suffix + '.bz2')
            print(f"COMPRESSED {file_path.name} → {new_path.name}")
            return new_path

        else:
            print(f"ERROR, not supported: {method}")
            return False

    except subprocess.CalledProcessError as e:
        print(f"ERROR, compressing file {file_path.name}: {e}")
        return False

########################### ENCRYPT

def encrypt_file(file_path: Path, encryption):
    try:
        if encryption['method'] == "gpg":
            encrypted_path = file_path.with_suffix(file_path.suffix + '.gpg')
            subprocess.run([
                'gpg',
                '--batch',
                '--yes',
                '--output', str(encrypted_path),
                '--encrypt',
                '--recipient', encryption['recipient'],
                str(file_path)
            ], check=True)

            print(f"ENCRYPTED {file_path.name} → {encrypted_path.name}")
            return encrypted_path
        else:
            print(f"ERROR, not supported: {encryption['method']}")


    except subprocess.CalledProcessError as e:
        print(f"ERROR encrypting file {file_path.name}: {e}")
        return False


############################ UPLOAD

def upload(file_path:Path, destination):
    try:
        if destination['method'] == "s3":
            s3 = boto3.client(
            's3',
            aws_access_key_id=destination['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=destination['AWS_SECRET_ACCESS_KEY'],
            )
            s3.upload_file(str(file_path), destination['S3_BUCKET'], file_path.name)
            print(f"UPLOADED {file_path.name} → s3://{destination['S3_BUCKET']}/{file_path.name}")
            return True
        
        else:
            print(f"ERROR, Not supported: {destination['method']}")
            return False
        
    except Exception as e:
        print(f"ERROR uploading to S3: {e}")
        return False

########################### MYSQLDUMP

def mysqldump(source, destinations, encryptions):
    for b in source:
        temp_dir = Path(source[b]['temp'])
        temp_dir.mkdir(parents=True, exist_ok=True)

        compress = source[b].get('compress')
        databases = source[b].get('databases')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')

        for db in databases:
            dump_file = temp_dir / f"{db}_{timestamp}.sql"
            cmd = ['mysqldump', db]

            with open(dump_file, 'wb') as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)

            if result.returncode != 0:
                print(f"Error dumping {db}: {result.stderr.decode()}")
                continue
            else:
                print(f"DUMPED {db} → {dump_file}")

            if compress:
                compressed_file = compress_file(dump_file, compress)
                if compressed_file:
                    dump_file = compressed_file

            if encryptions:
                encrypted_file = encrypt_file(dump_file, encryptions[source[b]['encryption']])
                if encrypted_file:
                    dump_file.unlink()
                    dump_file = encrypted_file
            
            if destinations:
                uploaded = upload(dump_file, destinations[source[b]['destination']])
                if uploaded:
                    dump_file.unlink()



BACKUP_SOURCES = {
    'mysqldump': mysqldump,
}

config_path = get_config_path()
config = load_config(config_path)
#print(config)

for source in config['sources']:
    BACKUP_SOURCES[source](config['sources'][source], config.get('destinations'), config.get('encryptions'))
