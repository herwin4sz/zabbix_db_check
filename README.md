# zabbix_db_check
A python script for database monitor with zabbix

The script use zabbix trapper protocol connect to zabbix server.
You may need to install some python modules before using the script. All of them can be found in pypi.org, use pip install is the easy way.

Modules:
  DBUtils
  APScheduler
  configparser

For ORACLE database:
  cx_Oracle
  
  To connect oracle you also need the "oracle_instantclient" which can be found in oracle web site and set env path "LD_LIBRARY_PATH". For example in linux you should add this to /etc/profile:
    export LD_LIBRARY_PATH=<some path you place the oracle_instantclient>
  
  
