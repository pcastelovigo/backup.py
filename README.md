# backup.py

Script de backups definido por YAML. Actualmente soporta `mysqldump`, compresion, cifrado GPG y subida a S3.
Incluye backup de directorios con `tar`.

## Requisitos

- Python 3.x
- PyYAML
- boto3
- Binarios disponibles en PATH segun lo que uses:
  - `mysqldump` y `mysql` (para MySQL)
  - `tar` (para directorios)
  - `gzip` o `bzip2` (si usas compresion)
  - `gpg` (si usas cifrado)

## Uso basico

```bash
python3 backup.py --config /ruta/al/config.yaml
```

Si no pasas `--config`, se usa `config.yaml` en el mismo directorio que `backup.py`.

## Configuracion YAML

Estructura general:

```yaml
sources:
  mysqldump:
    nombre_de_fuente:
      # opciones del backup (ver abajo)
  directories:
    nombre_de_fuente:
      # opciones del backup (ver abajo)

destinations:
  nombre_destino:
    # opciones de destino (ver abajo)

encryptions:
  nombre_cifrado:
    # opciones de cifrado (ver abajo)
```

### Sources: mysqldump

Opciones posibles para cada entrada de `sources.mysqldump.*`:

- `temp` (string, requerido): directorio temporal donde se guardan los dumps.
- `databases` (lista de strings, opcional): lista explicita de bases a dumpear.
- `all_databases_except_system` (bool, opcional): si es `true`, lista todas las bases y excluye las de sistema.
- `exclude_databases` (lista de strings, opcional): lista de bases a excluir cuando `all_databases_except_system` es `true`.
  - Por defecto: `mysql`, `information_schema`, `performance_schema`, `sys`.
- `compress` (string, opcional): `gzip` o `bzip2`.
- `encryption` (string, opcional): referencia a una entrada en `encryptions`.
- `destination` (string, opcional): referencia a una entrada en `destinations`.
- `cleanup` (bool, opcional): si es `true` (por defecto), borra el archivo local tras subirlo.
- `host` (string, opcional): host de MySQL.
- `port` (int, opcional): puerto de MySQL.
- `user` (string, opcional): usuario de MySQL.
- `password` (string, opcional): password de MySQL (se pasa via `MYSQL_PWD`).
- `extra_args` (lista de strings, opcional): argumentos extra para `mysqldump`.

Notas:
- Si `all_databases_except_system` es `true`, se ignora `databases` y se hace un dump individual por cada base listada.
- Si no se define `destination`, el archivo queda en `temp`.

Ejemplo con lista explicita:

```yaml
sources:
  mysqldump:
    prod:
      temp: /tmp/backups
      databases:
        - app
        - analytics
      compress: gzip
      encryption: gpg-main
      destination: s3-main
      cleanup: true
      host: localhost
      port: 3306
      user: root
      password: secret
      extra_args:
        - --single-transaction
        - --routines

destinations:
  s3-main:
    method: s3
    S3_BUCKET: my-bucket
    prefix: backups/mysql

encryptions:
  gpg-main:
    method: gpg
    recipient: backups@example.com
```

Ejemplo con todas las bases menos las de sistema:

```yaml
sources:
  mysqldump:
    prod:
      temp: /tmp/backups
      all_databases_except_system: true
      exclude_databases:
        - mysql
        - information_schema
        - performance_schema
        - sys
      destination: s3-main
```

### Destinations: s3

Opciones posibles para cada entrada de `destinations.*`:

- `method` (string, requerido): `s3`.
- `S3_BUCKET` (string, requerido): nombre del bucket.
- `prefix` (string, opcional): prefijo dentro del bucket.
- `AWS_ACCESS_KEY_ID` (string, opcional): credencial explicita.
- `AWS_SECRET_ACCESS_KEY` (string, opcional): credencial explicita.
- `AWS_SESSION_TOKEN` (string, opcional): token de sesion.

Si no se definen credenciales, boto3 usara su cadena de credenciales habitual (env vars, IAM role, etc.).

### Encryptions: gpg

Opciones posibles para cada entrada de `encryptions.*`:

- `method` (string, requerido): `gpg`.
- `recipient` (string, requerido): receptor GPG.

### Sources: directories

Opciones posibles para cada entrada de `sources.directories.*`:

- `path` (string, requerido): directorio a respaldar.
- `temp` (string, requerido): directorio temporal donde se guardan los tar.
- `compress` (string, opcional): `gzip`, `bzip2` o vacío.
- `encryption` (string, opcional): referencia a una entrada en `encryptions`.
- `destination` (string, opcional): referencia a una entrada en `destinations`.
- `cleanup` (bool, opcional): si es `true` (por defecto), borra el archivo local tras subirlo.
- `incremental` (bool, opcional): si es `true`, usa incremental de `tar`.
- `incremental_snapshot` (string, opcional): ruta al snapshot de incremental (`tar --listed-incremental`).

Notas:
- Si `incremental` es `true` necesitas `incremental_snapshot` para mantener el estado entre ejecuciones.
- El incremental genera un tar por ejecucion; el primer backup debe ser full (snapshot vacio o inexistente).

Ejemplo de directorio:

```yaml
sources:
  directories:
    home:
      path: /home/pablo/data
      temp: /tmp/backups
      compress: gzip
      destination: s3-main
```

Ejemplo incremental:

```yaml
sources:
  directories:
    home:
      path: /home/pablo/data
      temp: /tmp/backups
      compress: bzip2
      destination: s3-main
      incremental: true
      incremental_snapshot: /var/lib/backup/home.snar
```

## Salida y comportamiento

- Cada base genera un `.sql`.
- Si `compress` esta definido, el `.sql` se comprime (`.gz` o `.bz2`).
- Si `encryption` esta definido, el archivo se cifra (`.gpg`).
- Si `destination` esta definido, se sube el archivo al destino.
- Si `cleanup` es `true`, se borra el archivo local tras subirlo.

## Desarrollo y extension

El codigo esta organizado en clases:

- `BackupRunner`: orquesta el proceso.
- `BackupTask`: interfaz base.
- `MySQLDumpTask`: implementa el backup de MySQL.
- `Compressor`, `Encryptor`, `Uploader`: estrategias reutilizables.

Para anadir nuevas fuentes (Postgres, directorios, Percona, etc.), crea una nueva clase `BackupTask` y registrala en `BackupRunner.build_tasks`.
