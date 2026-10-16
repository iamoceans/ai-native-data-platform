#!/usr/bin/env python3
"""Launch a loopback-only Spark TLS fixture, optionally with a real identity.

The custom authenticator is for acceptance only. It grants no read-only policy
and uses an embedded metastore. All generated private files stay under runtime/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import xml.etree.ElementTree as ET

IMAGE = "apache/spark:3.5.3"
ROOT = Path(__file__).resolve().parents[2]
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
FILE_OWNER = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "root"


def docker(*arguments: str) -> None:
    env = dict(os.environ)
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    result = subprocess.run([*DOCKER, *arguments], env=env, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(result.stderr or "Docker fixture command failed")


def private_file(path: Path, value: str) -> None:
    with open(path, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--port", type=int, default=11001)
    parser.add_argument("--name", default="aind-spark-tls-acceptance")
    parser.add_argument("--auth", choices=("NOSASL", "PLAIN"), default="NOSASL")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    work = (args.output_dir or ROOT / "runtime" / "spark-tls-fixture").resolve()
    if not work.is_relative_to(ROOT / "runtime"):
        parser.error("output-dir must be inside the repository's ignored runtime/ directory")
    if work.exists() and any(work.iterdir()):
        parser.error("fixture directory is not empty; choose a new --output-dir")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    password = secrets.token_urlsafe(24)
    private_file(work / "keytool.env", "STORE_PASS=" + password + "\n")
    for command in (
        ["-genkeypair", "-alias", "server", "-keyalg", "RSA", "-keysize", "2048", "-validity", "2",
         "-dname", "CN=localhost", "-ext", "SAN=dns:localhost,ip:127.0.0.1", "-ext", "EKU=serverAuth",
         "-storetype", "JKS", "-keystore", "/fixture/server.jks", "-storepass:env", "STORE_PASS",
         "-keypass:env", "STORE_PASS", "-noprompt"],
        ["-exportcert", "-rfc", "-alias", "server", "-keystore", "/fixture/server.jks",
         "-storepass:env", "STORE_PASS", "-file", "/fixture/ca.pem"],
    ):
        docker("run", "--rm", "--user", FILE_OWNER, "--env-file", str(work / "keytool.env"),
               "-v", f"{work}:/fixture", "--entrypoint", "keytool", IMAGE, *command)
    (work / "server.jks").chmod(0o600)
    props = {
        "hive.server2.authentication": "NOSASL",
        "hive.server2.thrift.port": "10000",
        "hive.server2.thrift.bind.host": "0.0.0.0",
        "hive.server2.use.SSL": "true",
        "hive.server2.keystore.path": "/fixture/server.jks",
        "hive.server2.keystore.password": password,
    }
    if args.auth == "PLAIN":
        identity_password, salt = secrets.token_urlsafe(32), secrets.token_hex(32)
        private_file(work / "credentials.json", json.dumps({
            "username": "fixture-reader", "password": identity_password,
        }))
        digest = hashlib.sha256((salt + identity_password).encode()).hexdigest()
        private_file(work / "identity.properties", f"username=fixture-reader\nsalt={salt}\ndigest={digest}\n")
        (work / "classes").mkdir()
        # Use the compiler and Hive API shipped with the pinned Spark image.
        docker("run", "--rm", "--user", FILE_OWNER, "-v", f"{work}:/fixture",
               "-v", f"{Path(__file__).resolve().parent / 'auth'}:/auth:ro", "--entrypoint", "java",
               IMAGE, "-cp", "/opt/spark/jars/*", "scala.tools.nsc.Main", "-usejavacp",
               "-d", "/fixture/classes", "/auth/FixtureAuthenticationProvider.scala")
        props.update({
            "hive.server2.authentication": "CUSTOM",
            "hive.server2.custom.authentication.class": "fixture.FixtureAuthenticationProvider",
        })
    config = ET.Element("configuration")
    for name, value in props.items():
        prop = ET.SubElement(config, "property")
        ET.SubElement(prop, "name").text = name
        ET.SubElement(prop, "value").text = value
    private_file(work / "hive-site.xml", ET.tostring(config, encoding="unicode"))
    docker("run", "-d", "--name", args.name, "--user", "root", "--publish",
           f"127.0.0.1:{args.port}:10000", "--volume", f"{work}:/fixture:ro", "--volume",
           f"{work / 'hive-site.xml'}:/opt/spark/conf/hive-site.xml:ro",
           "--entrypoint", "/opt/spark/bin/spark-submit", IMAGE,
           "--driver-class-path", "/fixture/classes", "--class",
           "org.apache.spark.sql.hive.thriftserver.HiveThriftServer2", "--master", "local[2]",
           "--driver-memory", "1g", "--conf", "spark.ui.enabled=false",
           "--conf", "spark.sql.thriftServer.queryTimeout=120s",
           "--conf", "spark.sql.thriftServer.interruptOnCancel=true")
    print(f"Spark {args.auth} TLS fixture started; wait for server readiness. Public CA: {work / 'ca.pem'}")
    if args.auth == "PLAIN":
        print(f"Test-only identity file: {work / 'credentials.json'} (private; never print its contents)")


if __name__ == "__main__":
    main()
