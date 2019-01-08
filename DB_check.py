#!/usr/bin/python
# -*- coding: UTF-8 -*-

import cx_Oracle
import configparser
import datetime
import logging.handlers
import json
import time
import sys
import os
import signal
import socket
import struct
from DBUtils.PooledDB import PooledDB
from apscheduler.schedulers.background import BackgroundScheduler


# init logging function
def init_logging(name):
    log_path = sys.path[0] + "/logs"
    if not os.path.exists(log_path):
        os.makedirs(log_path)
    lfname = log_path + "/" + name
    global logger
    logger = logging.getLogger()
    LOG_LEVELS = {'debug': logging.DEBUG,
                  'info': logging.INFO,
                  'warning': logging.WARNING,
                  'error': logging.ERROR,
                  'critical': logging.CRITICAL}
    logger.setLevel(LOG_LEVELS.get(log_level, logging.INFO))
    logfilehandler = logging.handlers.TimedRotatingFileHandler(lfname, when='D', interval=1, backupCount=5)
    logfilehandler.suffix = "%Y%m%d.log"
    logfilehandler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s]: %(message)s"))
    logger.addHandler(logfilehandler)
    logstdhandler = logging.StreamHandler(sys.stdout)
    logstdhandler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s]: %(message)s"))
    logger.addHandler(logstdhandler)


# init database logging
def init_database_logging():
    try:
        global log_level
        log_level = config['base']['log_level']
    except KeyError:
        log_level = "info"
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    try:
        init_logging("database-checker-" + config['database']['name'])
    except KeyError, key:
        logger.error(str(key) + " is not defined")
        thread_watcher.kill()
        sys.exit(str(key) + " is not defined")


# Create watcher thread to kill all thread when interrupted
class Watcher():
    def __init__(self):
        self.child = os.fork()
        if self.child == 0:
            return
        else:
            self.watch()

    def watch(self):
        try:
            os.wait()
        except KeyboardInterrupt:
            self.kill()
        except Exception:
            self.kill()
        sys.exit()

    def kill(self):
        try:
            os.kill(self.child, signal.SIGKILL)
        except OSError:
            pass


# Load config file
def load_config():
    try:
        config.read(sys.argv[1])
        logger.info("Loaded config from " + sys.argv[1])
    except IndexError, Argument:
        logger.error("No config file assign.")
        logger.error("Usage: ./DB_check.py <config file>")
        thread_watcher.kill()
        sys.exit("No config file assign. Usage: ./DB_check.py <config file>")


# Init database connection pool
def init_db_pool():
    try:
        db_type = str.upper(str(config['database']['type']))
    except KeyError, key:
        logger.error(str(key) + " is not defined")
        thread_watcher.kill()
        sys.exit(str(key) + " is not defined")

    # For Oracle database
    if db_type == "ORACLE":
        try:
            db_ip = config['database']['ip']
            db_port = config['database']['port']
            db_sid = config['database']['sid']
            db_user = config['database']['user']
            db_password = config['database']['password']
        except KeyError, key:
            logger.error(str(key) + " is not defined")
            thread_watcher.kill()
            sys.exit(str(key) + " is not defined")
        global dbpool
        dbpool = init_oracle_conn(db_ip, db_port, db_sid, db_user, db_password)
    else:
        logger.error("Unsupported database type: " + str(config['database']['type']))
        thread_watcher.kill()
        sys.exit("Unsupported database type: " + str(config['database']['type']))


# Create Oracle database connection
def init_oracle_conn(ip, port, sid, user, password):
    conn_count = 0
    dsn = cx_Oracle.makedsn(ip, port, sid)
    while True:
        try:
            pool = PooledDB(creator=cx_Oracle, user=user, password=password, dsn=dsn, threaded=True, mincached=1,
                            maxcached=5, maxconnections=5)
            logger.info("Database(" + ip + ":" + str(port) + ") connected")
        except cx_Oracle.DatabaseError as err_msg:
            conn_count += 1
            if conn_count > 1:
                logger.error("Failure to establish database(" + ip + ":" + str(port) + ") connection." + str(err_msg))
                print err_msg
                thread_watcher.kill()
                sys.exit("Failure to establish database(" + ip + ":" + str(port) + ") connection." )
            logger.error("Database(" + ip + ":" + str(port) + ") connection failed. Waiting for retry.")
            time.sleep(10)
            continue
        break
    return pool


# Process check item in config file
def process_items():
    for item in config:
        if item in ["DEFAULT", "base", "database", ""]:
            continue
        elif str.upper(str(config[item]['type'])) == 'S':
            try:
                if str(config[item]['item_name']) == "":
                    logger.error("[" + str(item) + "] parameter <item_name> is not defined. Item will be skipped")
                    continue
            except KeyError:
                logger.error("[" + str(item) + "] parameter <item_name> is not defined. Item will be skipped")
                continue
            #single_item_check(config[item])
            single_item_add(config[item])
            continue
        elif str.upper(str(config[item]['type'])) == 'M':
            try:
                if str(config[item]['item_name']) == "":
                    logger.error("[" + str(item) + "] parameter <item_name> is not defined. Item will be skipped")
                    continue
            except KeyError:
                logger.error("[" + str(item) + "] parameter <item_name> is not defined. Item will be skipped")
                continue
            #multi_item_check(config[item])
            multi_item_add(config[item])
            continue
        else:
            logger.error("Item: " + str(config[item]) + " parameter <type> is not defined. Item will be skipped")
            continue


# Add check job of single item type to scheduler
def single_item_add(item):
    logger.info("Add check job <" + str(item) + "> to scheduler")

    try:
        trigger = str.upper(str(item['trigger']))
    except KeyError, key:
        logger.error(str(key) + " is not defined. Item " + str(item) + " will be skipped")
        return

    if trigger == "INTERVAL":
        try:
            scheduler.add_job(single_item_check, args=[item], trigger='interval', seconds=int(item['interval']),
                          id=str(item), replace_existing=True, next_run_time=datetime.datetime.now())
        except BaseException, Argument:
            logger.error("Adding job <" + str(item) + "> is failed: " + str(Argument))
            return

    elif trigger == "CRON":
        try:
            hour = item['hour']
            minute = item['minute']
        except KeyError, key:
            logger.error(str(key) + " is not defined. Item " + str(item) + " will be skipped")
            return
        try:
            scheduler.add_job(single_item_check, args=[item], trigger='cron', hour=hour, minute=minute,
                              id=str(item), replace_existing=True)
        except BaseException, Argument:
            logger.error("Adding job <" + str(item) + "> is failed: " + str(Argument))
            return

    else:
        logger.error(str(item) + " trigger is not defined. Item will be skipped")
        return


# Add check job of multi item type to scheduler
def multi_item_add(item):
    logger.info("Add check job <" + str(item) + "> to scheduler")

    try:
        trigger = str.upper(str(item['trigger']))
    except KeyError, key:
        logger.error(str(key) + " is not defined. Item " + str(item) + " will be skipped")
        return

    if trigger == "INTERVAL":
        try:
            scheduler.add_job(multi_item_check, args=[item], trigger='interval', seconds=int(item['interval']),
                          id=str(item), replace_existing=True, next_run_time=datetime.datetime.now())
        except BaseException, Argument:
            logger.error("Adding job <" + str(item) + "> is failed: " + str(Argument))
            return

    elif trigger == "CRON":
        try:
            hour = item['hour']
            minute = item['minute']
        except KeyError, key:
            logger.error(str(key) + " is not defined. Item " + str(item) + " will be skipped")
            return
        try:
            scheduler.add_job(multi_item_check, args=[item], trigger='cron', hour=hour, minute=minute,
                              id=str(item), replace_existing=True)
        except BaseException, Argument:
            logger.error("Adding job <" + str(item) + "> is failed: " + str(Argument))
            return

    else:
        logger.error(str(item) + " trigger is not defined. Item will be skipped")
        return


# Check job for single item type
def single_item_check(item):
    # Get check result
    try:
        conn = dbpool.connection()
        cur = conn.cursor()
        logger.debug("Execute SQL: " + item['sql'])
        cur.execute(item['sql'])
        if cur.rowcount == 0:
            res = "Null"
        else:
            res = cur.fetchone()[0]
        logger.debug("SQL return: " + str(res))
        cur.close()
        conn.close()
    except BaseException, Argument:
        logger.error("Check job: " + str(item) + "failed: " + str(Argument))
        return

    # convert result to zabbix data form and send
    res_data = []
    res_data.append({"host": host_name, "key": item['item_name'], "value": res})

    zabbix_sender(res_data)


# Check job for multi item type
def multi_item_check(item):
    res_data = []
    try:
        is_auto_discover = str.upper(str(item['auto_discover']))
    except KeyError:
        logger.debug("[" + str(item) + "] parameter <auto_discover> is not defined." )
        is_auto_discover = "FALSE "
        pass

    if is_auto_discover == "TRUE":
        key_list = []
        key_index = "{#" + str.upper(str(item['item_name'])) + "}"

    # Get check result
    try:
        conn = dbpool.connection()
        cur = conn.cursor()
        logger.debug("Execute SQL: " + item['sql'])
        cur.execute(item['sql'])
        key_title = [i[0] for i in cur.description]
        while True:
            res = cur.fetchone()
            if not res:
                break
            # Convert result to zabbix from
            i = 1
            while i < len(key_title):
                key_name = key_title[i] + "[" + res[0] + "]"
                key_value = str(res[i])
                res_data.append({"host": host_name, "key": key_name, "value": key_value})
                i = i + 1
            # Convert key to zabbix from if auto discover is needed
            if is_auto_discover == "TRUE":
                key_list.append({key_index: res[0]})
    except BaseException, Argument:
        logger.error("Check job: " + str(item) + "failed: " + str(Argument))
        return

    # Send data to zabbix server
    # Send key name data
    if is_auto_discover == "TRUE":
        key_data = [{"host": host_name, "key": item['item_name'], "value": json.dumps({"data": key_list})}]
        zabbix_sender(key_data)
    # Send key value data
    zabbix_sender(res_data)


# Define zabbix parameters
def load_zabbix_config():
    try:
        global zabbix_server, zabbix_port, host_name
        zabbix_server = config['base']['zabbix_server']
        zabbix_port = int(config['base']['zabbix_port'])
        host_name = config['database']['name']
    except KeyError, key:
        logger.error(str(key) + " is not defined")
        thread_watcher.kill()
        sys.exit(str(key) + " is not defined")


# Send data to zabbix server
def zabbix_sender(data):
    zabbix_data = json.dumps({"request": "sender data", "data": data})
    packet = "ZBXD\1" + struct.pack('<Q', len(zabbix_data)) + zabbix_data

    try:
        so = socket.socket()
        so.connect((zabbix_server, zabbix_port))
        so.send(packet)
        logger.debug("Send zabbix data packet: " + packet)
        logger.debug("Receive zabbix server response: " + so.recv(1024))
    except BaseException, Argument:
        logger.error("Fail to send data to " + zabbix_server + ":" + str(zabbix_port) + " " + str(Argument))
        pass


if __name__ == '__main__':
    # initial work path
    os.chdir(sys.path[0])
    thread_watcher = Watcher()

    # Set base parameters
    log_level = "info"

    # Database encode dictionary
    encode_dict = {
        'SIMPLIFIED CHINESE_CHINA.ZHS16GBK': 'GBK',
        'AMERICAN_AMERICA.ZHS16GBK': 'GBK'
    }

    # initialise logging
    init_logging("database-checker")

    # Load config from config file
    config = configparser.ConfigParser()
    load_config()

    # Load logging with config level and set to database log file
    init_database_logging()

    # Load some base config
    load_zabbix_config()

    # Create DB connector pool
    init_db_pool()

    # Process check items one by one
    # Initial job scheduler
    scheduler = BackgroundScheduler()
    process_items()
    scheduler.start()

    try:
        check_point_time = int(time.time())
        try:
            reload_interval = float(config['base']['interval'])
        except KeyError:
            logger.warning("Config file reload interval is not defined. Use default set as 30 seconds")
            reload_interval = 30
        while True:
            modify_time = int(os.path.getmtime(sys.argv[1]))
            if modify_time > check_point_time:
                logger.info("Config file " + sys.argv[1] + " is modified. Config will be reload.")
                # Redo all the loadding
                load_config()
                init_database_logging()
                load_zabbix_config()
                init_db_pool()
                scheduler.remove_all_jobs()
                process_items()
                check_point_time = int(time.time())

            time.sleep(reload_interval)

    except KeyboardInterrupt:
        pass

