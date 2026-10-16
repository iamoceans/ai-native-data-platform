#!/usr/bin/env python3
"""Generate ephemeral local certificates and launch the pinned Spark TLS fixture.

Output includes only public CA location. Keystore password files remain under
runtime/ (gitignored); never use this fixture's NOSASL identity as authorization.
"""
import json,os,secrets,subprocess
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description="Local pinned Spark TLS fixture; never a source authorization deployment")
parser.add_argument('--output-dir', type=Path)
parser.add_argument('--port', type=int, default=11001)
parser.add_argument('--name', default='aind-spark-tls-acceptance')
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
work=args.output_dir or root/'runtime'/'spark-tls-fixture'
work=work.resolve()
work.mkdir(parents=True,exist_ok=True)
if (work/'server.jks').exists():
    raise SystemExit('Fixture already exists; reuse it or select a new --output-dir.')
port=args.port
container_name=args.name
pw=secrets.token_urlsafe(24)
envfile=work/'keytool.env';envfile.write_text('STORE_PASS='+pw+'\n');envfile.chmod(0o600)
docker=['env','-u','DOCKER_HOST','-u','DOCKER_CONTEXT','-u','DOCKER_TLS','-u','DOCKER_TLS_VERIFY','-u','DOCKER_CERT_PATH','docker','--host=unix:///var/run/docker.sock']
commands=[['keytool','-genkeypair','-alias','server','-keyalg','RSA','-keysize','2048','-validity','2','-dname','CN=localhost','-ext','SAN=dns:localhost,ip:127.0.0.1','-ext','EKU=serverAuth','-storetype','JKS','-keystore','/fixture/server.jks','-storepass:env','STORE_PASS','-keypass:env','STORE_PASS','-noprompt'],['keytool','-exportcert','-rfc','-alias','server','-keystore','/fixture/server.jks','-storepass:env','STORE_PASS','-file','/fixture/ca.pem']]
for command in commands:
 result=subprocess.run(docker+['run','--rm','--user','root','--env-file',str(envfile),'-v',str(work)+':/fixture','--entrypoint',command[0],'apache/spark:3.5.3']+command[1:],capture_output=True,text=True)
 if result.returncode:print(result.stderr);raise SystemExit(result.returncode)
props={'hive.server2.authentication':'NOSASL','hive.server2.thrift.port':'10000','hive.server2.thrift.bind.host':'0.0.0.0','hive.server2.use.SSL':'true','hive.server2.keystore.path':'/fixture/server.jks','hive.server2.keystore.password':pw}
(work/'hive-site.xml').write_text('<configuration>'+''.join('<property><name>'+k+'</name><value>'+v+'</value></property>' for k,v in props.items())+'</configuration>')
(work/'hive-site.xml').chmod(0o600)
args=docker+['run','-d','--name',container_name,'--user','root','--publish',f'127.0.0.1:{port}:10000','--volume',str(work)+':/fixture:ro','--volume',str(work/'hive-site.xml')+':/opt/spark/conf/hive-site.xml:ro','--entrypoint','/opt/spark/bin/spark-submit','apache/spark:3.5.3','--class','org.apache.spark.sql.hive.thriftserver.HiveThriftServer2','--master','local[2]','--driver-memory','1g','--conf','spark.ui.enabled=false']
result=subprocess.run(args,capture_output=True,text=True)
print('TLS fixture container started; public CA: '+str(work/'ca.pem') if result.returncode==0 else result.stderr)
raise SystemExit(result.returncode)
