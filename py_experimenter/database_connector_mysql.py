import logging
from logging import Logger
from typing import Dict, List, Tuple

import numpy as np
import sshtunnel
from omegaconf import OmegaConf
from pymysql import Error, connect

from py_experimenter.database_connector import DatabaseConnector
from py_experimenter.exceptions import (
    DatabaseConnectionError,
    DatabaseCreationError,
    SshTunnelError,
)


class DatabaseConnectorMYSQL(DatabaseConnector):
    _prepared_statement_placeholder = "%s"

    def __init__(self, database_configuration: OmegaConf, use_codecarbon: bool, credential_path: str, use_ssh_tunnel: bool, logger: Logger):
        self.credential_path = credential_path
        self.use_ssh_tunnel = use_ssh_tunnel
        if self.use_ssh_tunnel:
            self.start_ssh_tunnel(logger)
        super().__init__(database_configuration, use_codecarbon, logger)

    def get_ssh_tunnel(self, logger: Logger):
        credentials = OmegaConf.load(self.credential_path)["CREDENTIALS"]["Connection"]
        if "Ssh" in credentials:
            parameters = dict(credentials["Ssh"])
            ssh_address_or_host = parameters["address"]
            ssh_address_or_host_port = parameters["port"] if "port" in parameters else 22
            ssh_keypass = parameters["ssh_keypass"] if "ssh_keypass" in parameters else None
            remote_bind_address = parameters["remote_address"] if "remote_address" in parameters else "127.0.0.1"
            remote_bind_address_port = parameters["remote_port"] if "remote_port" in parameters else 3306
            local_bind_address = parameters["local_address"] if "local_address" in parameters else "127.0.0.1"
            local_bind_address_port = parameters["local_port"] if "local_port" in parameters else 3306

            try:
                tunnel = sshtunnel.SSHTunnelForwarder(
                    ssh_address_or_host=(ssh_address_or_host, ssh_address_or_host_port),
                    ssh_pkey=ssh_keypass,
                    remote_bind_address=(remote_bind_address, remote_bind_address_port),
                    local_bind_address=(local_bind_address, local_bind_address_port),
                    logger=logger,
                )
            except Exception as err:
                logger.error(err)
                raise SshTunnelError(err)
            return tunnel
        else:
            return None

    def start_ssh_tunnel(self, logger: Logger):
        tunnel = self.get_ssh_tunnel(logger)
        # Tunnels may not be stopepd instantly, so we check if the tunnel is active before starting it
        if tunnel is not None and not tunnel.is_active:
            tunnel.start()

    def close_ssh_tunnel(self):
        tunnel = self.get_ssh_tunnel(self.logger)
        if tunnel is not None:
            tunnel.stop(force=True)

    def _test_connection(self):
        try:
            connection = self.connect()
        except Exception as err:
            logging.error(err)
            raise DatabaseConnectionError(err)
        else:
            self.close_connection(connection)

    def _create_database_if_not_existing(self):
        try:
            connection = self.connect()
            cursor = self.cursor(connection)
            self.execute(cursor, "SHOW DATABASES")
            databases = [database[0] for database in self.fetchall(cursor)]

            if self.database_configuration.database_name not in databases:
                self.execute(cursor, f"CREATE DATABASE {self.database_configuration.database_name}")
                self.commit(connection)
            self.close_connection(connection)
        except Exception as err:
            raise DatabaseCreationError(f"Error when creating database: \n {err}")

    def connect(self):
        credentials = dict(self._get_database_credentials())
        try:
            return connect(**credentials)
        except Error as err:
            raise DatabaseConnectionError(err)
        finally:
            credentials = None

    def close_connection(self, connection):
        closed_connection = super().close_connection(connection)
        return closed_connection

    def _get_database_credentials(self):
        try:
            credential_config = OmegaConf.load(self.credential_path)
            database_configuration = credential_config["CREDENTIALS"]["Database"]
            if self.use_ssh_tunnel:
                server_address = credential_config["CREDENTIALS"]["Connection"]["Ssh"]["server"]
            else:
                server_address = credential_config["CREDENTIALS"]["Connection"]["Standard"]["server"]
            credentials = {
                "host": server_address,
                "user": database_configuration["user"],
                "password": database_configuration["password"],
            }

            return {
                **credentials,
                "database": self.database_configuration.database_name,
            }
        except Exception as err:
            logging.error(err)
            raise DatabaseCreationError("Invalid credentials file!")

    def _start_transaction(self, connection, readonly=False):
        if not readonly:
            connection.begin()

    def _table_exists(self, cursor, table_name: str = None) -> bool:
        table_name = table_name if table_name is not None else self.database_configuration.table_name
        self.execute(cursor, f"SHOW TABLES LIKE '{table_name}'")
        return self.fetchall(cursor)

    @staticmethod
    def get_autoincrement():
        return "AUTO_INCREMENT"

    def _table_has_correct_structure(self, cursor, typed_fields: Dict[str, str]):
        self.execute(
            cursor,
            f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = {self._prepared_statement_placeholder} AND TABLE_SCHEMA = {self._prepared_statement_placeholder}",
            (self.database_configuration.table_name, self.database_configuration.database_name),
        )
        columns = self.fetchall(cursor)
        columns = self._exclude_fixed_columns([column[0] for column in columns])
        return set(columns) == set(typed_fields.keys())

    def _pull_open_experiment(self, random_order) -> Tuple[int, List, List]:
        try:
            connection = self.connect()
            cursor = self.cursor(connection)
            self._start_transaction(connection, readonly=False)
            experiment_id, description, values = self._select_open_experiments_from_db(connection, cursor, random_order=random_order)
        except Exception as err:
            connection.rollback()
            raise err
        finally:
            self.close_connection(connection)

        return experiment_id, description, values

    def _get_pull_experiment_query(self, order_by: str):
        return super()._get_pull_experiment_query(order_by) + " FOR UPDATE;"

    @staticmethod
    def random_order_string():
        return "RAND()"

    def _get_existing_rows(self, column_names):
        connection = self.connect()
        cursor = self.cursor(connection)
        self.execute(cursor, f"SELECT {','.join(column_names)} FROM {self.database_configuration.table_name}")
        values = self.fetchall(cursor)
        self.close_connection(connection)
        return [dict(zip(column_names, existing_row)) for existing_row in values]

    def get_structure_from_table(self, cursor):
        def _get_column_names_from_entries(entries):
            return [entry[0] for entry in entries]

        self.execute(cursor, f"SHOW COLUMNS FROM {self.database_configuration.table_name}")
        column_names = _get_column_names_from_entries(self.fetchall(cursor))
        return column_names