import math
import pytest
import sqlglot
from sqlglot import exp
from app.providers.sql_prep import bind_spark_parameters
from app.datasource.config_models import SparkConnectionConfig
from app.providers.spark import SparkProvider
from app.providers.base import ProviderCredentials


@pytest.mark.parametrize('value',["x'; DROP TABLE demo.orders; --",r"x\'; SELECT secret FROM forbidden",'a\nb',':another',None,True,False,12,-2,1.5])
def test_bound_values_are_literals_and_cannot_change_the_statement(value):
    tree=sqlglot.parse_one(bind_spark_parameters('SELECT :value AS result',{'value':value}),read='spark')
    assert len(list(tree.find_all(exp.Select)))==1
    assert not list(tree.find_all(exp.Table))
    if isinstance(value,str):assert next(tree.find_all(exp.Literal)).this==value


@pytest.mark.parametrize('value',[{},[],float('inf'),float('nan')])
def test_binding_rejects_non_scalar_or_nonfinite_values(value):
    with pytest.raises(ValueError):bind_spark_parameters('SELECT :x',{'x':value})


def test_tls_and_plain_are_forwarded_with_verification(monkeypatch):
    captured={}
    monkeypatch.setattr('app.providers.spark.connect',lambda **kw:captured.update(kw))
    config={'host':'localhost','port':11001,'database':'default','metastore_host':'localhost','use_ssl':True,'auth_mechanism':'PLAIN','ca_cert':'/tmp/fixture-ca.pem'}
    config=SparkConnectionConfig.model_validate(config).model_dump()
    SparkProvider(connection_config=config,credentials=ProviderCredentials('reader','fixture-password'))._connect()
    assert captured['verify_cert'] is True and captured['use_ssl'] is True
    assert captured['auth_mechanism']=='PLAIN' and captured['ca_cert']=='/tmp/fixture-ca.pem'
    config['use_ssl']=False
    with pytest.raises(ValueError):SparkConnectionConfig.model_validate(config)


@pytest.mark.parametrize('sql,parameters',[('SELECT ?',{}),('SELECT :x',{}),('SELECT 1',{'extra':'value'})])
def test_missing_anonymous_or_extra_bindings_fail_closed(sql,parameters):
    with pytest.raises(ValueError):bind_spark_parameters(sql,parameters)
