import os
import stat
import tempfile
import threading
import time
import uuid
import pytest

from decimal import Decimal

import nzpy_extended as nzpy

pytestmark = pytest.mark.full

class TestExternalTableImport:

    @pytest.fixture(autouse=True)
    def setup(self, con, cursor):
        self.con = con
        self.cursor = cursor
        self.working_dir = tempfile.gettempdir()
        self.test_files = []

        yield

        for filepath in self.test_files:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

    def _create_test_file(self, filename, content):
        filepath = os.path.join(self.working_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(content)
        self.test_files.append(filepath)
        return filepath

    @pytest.mark.asyncio
    async def test_import_latin1_encoding(self):
        await self.cursor.execute("DROP TABLE test_import_latin1 IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_import_latin1 (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)

        filepath = self._create_test_file(
            'test_import_latin1.csv',
            b'1,S\xfc\xdfes Caf\xe9\n2,\xc4pfel\n'
        )

        await self.cursor.execute(f"""
            INSERT INTO test_import_latin1 SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_import_latin1
            USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)

        await self.cursor.execute("SELECT * FROM test_import_latin1 ORDER BY id")
        rows = await self.cursor.fetchall()

        assert len(rows) == 2
        assert rows[0][0] == 1
        assert 'S' in rows[0][1] and 'Caf' in rows[0][1]
        assert rows[1][0] == 2
        assert 'pfel' in rows[1][1]

    @pytest.mark.asyncio
    async def test_import_utf8_encoding(self):
        await self.cursor.execute("DROP TABLE test_import_utf8 IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_import_utf8 (
                id INT,
                text_data NVARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)

        filepath = self._create_test_file(
            'test_import_utf8.csv',
            '1,Hello 世界\n2,Привет мир\n'.encode('utf-8')
        )

        await self.cursor.execute(f"""
            INSERT INTO test_import_utf8 SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_import_utf8
            USING (
                ENCODING 'UTF8'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)

        await self.cursor.execute("SELECT * FROM test_import_utf8 ORDER BY id")
        rows = await self.cursor.fetchall()

        assert len(rows) == 2
        assert rows[0][0] == 1
        assert 'Hello' in rows[0][1]
        assert rows[1][0] == 2

    @pytest.mark.asyncio
    async def test_import_internal_encoding(self):
        await self.cursor.execute("DROP TABLE test_import_internal IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_import_internal (
                col1 VARCHAR(100),
                col2 NVARCHAR(100)
            ) DISTRIBUTE ON RANDOM
        """)

        filepath = self._create_test_file(
            'test_import_internal.csv',
            b'S\xfc\xdfes oder h\xe4ssliches Encoding?,S\xc3\xbc\xc3\x9fes oder h\xc3\xa4ssliches Encoding?'
        )

        await self.cursor.execute(f"""
            INSERT INTO test_import_internal SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_import_internal
            USING (
                ENCODING 'internal'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)

        await self.cursor.execute("SELECT * FROM test_import_internal")
        rows = await self.cursor.fetchall()

        assert len(rows) == 1
        assert 'oder' in rows[0][0]
        assert 'oder' in rows[0][1]

    @pytest.mark.asyncio
    async def test_import_ascii_encoding(self):
        await self.cursor.execute("DROP TABLE test_import_ascii IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_import_ascii (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)

        filepath = self._create_test_file(
            'test_import_ascii.csv',
            b'1,Hello World\n2,Test Data\n3,Simple Text\n'
        )

        await self.cursor.execute(f"""
            INSERT INTO test_import_ascii SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_import_ascii
            USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)

        await self.cursor.execute("SELECT COUNT(*) FROM test_import_ascii")
        count = (await self.cursor.fetchone())[0]

        assert count == 3

    @pytest.mark.asyncio
    async def test_import_large_file(self):
        await self.cursor.execute("DROP TABLE test_import_large IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_import_large (
                id INT,
                text_data NVARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)

        content = []
        for i in range(1000):
            content.append(f'{i},Test data row {i}\n'.encode('utf-8'))

        filepath = self._create_test_file(
            'test_import_large.csv',
            b''.join(content)
        )

        await self.cursor.execute(f"""
            INSERT INTO test_import_large SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_import_large
            USING (
                ENCODING 'UTF8'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)

        await self.cursor.execute("SELECT COUNT(*) FROM test_import_large")
        count = (await self.cursor.fetchone())[0]

        assert count == 1000

    @pytest.mark.asyncio
    async def test_import_empty_file(self):
        await self.cursor.execute("DROP TABLE test_import_empty IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_import_empty (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)
        
        filepath = self._create_test_file('test_import_empty.csv', b'')
        
        await self.cursor.execute(f"""
            INSERT INTO test_import_empty SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_import_empty
            USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)
        
        await self.cursor.execute("SELECT COUNT(*) FROM test_import_empty")
        count = (await self.cursor.fetchone())[0]
        
        assert count == 0


class TestExternalTableExport:
    
    @pytest.fixture(autouse=True)
    def setup(self, con, cursor):
        self.con = con
        self.cursor = cursor
        self.working_dir = tempfile.gettempdir()
        self.test_files = []
        
        yield
        
        for filepath in self.test_files:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
    
    def _track_file(self, filename):
        filepath = os.path.join(self.working_dir, filename)
        self.test_files.append(filepath)
        return filepath
    
    @pytest.mark.asyncio
    async def test_export_latin1_encoding(self):
        await self.cursor.execute("DROP TABLE test_export_latin1 IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_export_latin1 (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)
        await self.cursor.execute("""
            INSERT INTO test_export_latin1 VALUES
            (1, 'Süßes Café')
        """)
        await self.cursor.execute("""
            INSERT INTO test_export_latin1 VALUES
            (2, 'Äpfel')
        """)
        
        export_file = 'test_export_latin1.csv'
        filepath = self._track_file(export_file)
        
        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{filepath}' USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            ) AS SELECT * FROM test_export_latin1
        """)
        
        assert os.path.exists(filepath)
        with open(filepath, 'rb') as f:
            content = f.read()
            assert len(content) > 0
            assert b'Caf' in content or b'pfel' in content
    
    @pytest.mark.asyncio
    async def test_export_utf8_encoding(self):
        await self.cursor.execute("DROP TABLE test_export_utf8 IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_export_utf8 (
                id INT,
                text_data NVARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)
        await self.cursor.execute("""
            INSERT INTO test_export_utf8 VALUES
            (1, 'Hello World')
        """)
        await self.cursor.execute("""
            INSERT INTO test_export_utf8 VALUES
            (2, 'Test Data')
        """)
        
        export_file = 'test_export_utf8.csv'
        filepath = self._track_file(export_file)
        
        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{filepath}' USING (
                ENCODING 'UTF8'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            ) AS SELECT * FROM test_export_utf8
        """)
        
        assert os.path.exists(filepath)
        with open(filepath, 'rb') as f:
            content = f.read()
            assert len(content) > 0
            assert b'Hello' in content or b'Test' in content
    
    @pytest.mark.asyncio
    async def test_export_internal_encoding(self):
        await self.cursor.execute("DROP TABLE test_export_internal IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_export_internal (
                col1 VARCHAR(100),
                col2 NVARCHAR(100)
            ) DISTRIBUTE ON RANDOM
        """)
        await self.cursor.execute("""
            INSERT INTO test_export_internal VALUES
            ('Test Data', 'Test Data')
        """)
        
        export_file = 'test_export_internal.csv'
        filepath = self._track_file(export_file)
        
        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{filepath}' USING (
                ENCODING 'internal'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            ) AS SELECT * FROM test_export_internal
        """)

        assert os.path.exists(filepath)
        with open(filepath, 'rb') as f:
            content = f.read()
            assert len(content) > 0
            assert b'Test' in content

    @pytest.mark.asyncio
    async def test_export_large_dataset(self):
        await self.cursor.execute("DROP TABLE test_export_large IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_export_large (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)

        for i in range(20):
            await self.cursor.execute(
                f"INSERT INTO test_export_large VALUES ({i}, 'Test data row {i}')"
            )

        export_file = 'test_export_large.csv'
        filepath = self._track_file(export_file)

        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{filepath}' USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            ) AS SELECT * FROM test_export_large
        """)

        assert os.path.exists(filepath)
        file_size = os.path.getsize(filepath)
        assert file_size > 200

    @pytest.mark.asyncio
    async def test_export_empty_table(self):
        await self.cursor.execute("DROP TABLE test_export_empty IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_export_empty (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)

        export_file = 'test_export_empty.csv'
        filepath = self._track_file(export_file)

        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{filepath}' USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            ) AS SELECT * FROM test_export_empty
        """)

        assert os.path.exists(filepath)


class TestExternalTableFIFO:

    @pytest.fixture(autouse=True)
    def setup(self, con, cursor):
        self.con = con
        self.cursor = cursor
        self.working_dir = tempfile.gettempdir()
        self.test_fifos = []

        yield

        for fifo_path in self.test_fifos:
            if os.path.exists(fifo_path):
                try:
                    os.remove(fifo_path)
                except Exception:
                    pass

    @pytest.mark.skipif(not hasattr(os, 'mkfifo'), 
                       reason="FIFOs not supported on this platform")
    @pytest.mark.asyncio
    async def test_import_from_fifo(self):
        fifo_path = os.path.join(self.working_dir, 'test_import.fifo')
        self.test_fifos.append(fifo_path)

        if os.path.exists(fifo_path):
            os.remove(fifo_path)
        os.mkfifo(fifo_path)

        await self.cursor.execute("DROP TABLE test_fifo_import IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_fifo_import (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)
        
        def write_to_fifo():
            time.sleep(1)
            with open(fifo_path, 'wb') as f:
                f.write(b'1,FIFO Test Data\n')
                f.write(b'2,Streaming Import\n')
        
        writer_thread = threading.Thread(target=write_to_fifo)
        writer_thread.start()
        
        await self.cursor.execute(f"""
            INSERT INTO test_fifo_import SELECT *
            FROM EXTERNAL '{fifo_path}' SAMEAS test_fifo_import
            USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)
        
        writer_thread.join()
        
        await self.cursor.execute("SELECT COUNT(*) FROM test_fifo_import")
        count = (await self.cursor.fetchone())[0]
        
        assert count == 2
    
    @pytest.mark.skipif(not hasattr(os, 'mkfifo'),
                       reason="FIFOs not supported on this platform")
    @pytest.mark.asyncio
    async def test_export_to_fifo(self):
        fifo_path = os.path.join(self.working_dir, 'test_export.fifo')
        self.test_fifos.append(fifo_path)
        
        if os.path.exists(fifo_path):
            os.remove(fifo_path)
        os.mkfifo(fifo_path)

        await self.cursor.execute("DROP TABLE test_fifo_export IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_fifo_export (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)
        await self.cursor.execute("""
            INSERT INTO test_fifo_export VALUES
            (1, 'FIFO Export Test')
        """)
        await self.cursor.execute("""
            INSERT INTO test_fifo_export VALUES
            (2, 'Streaming Output')
        """)

        exported_data = []
        def read_from_fifo():
            time.sleep(1)
            with open(fifo_path, 'rb') as f:
                data = f.read()
                exported_data.append(data)

        reader_thread = threading.Thread(target=read_from_fifo)
        reader_thread.start()

        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{fifo_path}' USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            ) AS SELECT * FROM test_fifo_export
        """)

        reader_thread.join()

        assert len(exported_data) > 0
        assert len(exported_data[0]) > 0


class TestExternalTableEdgeCases:

    @pytest.fixture(autouse=True)
    def setup(self, con, cursor):
        self.con = con
        self.cursor = cursor
        self.working_dir = tempfile.gettempdir()
        self.test_files = []
        
        yield
        
        for filepath in self.test_files:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
    
    @pytest.mark.asyncio
    async def test_import_file_with_no_trailing_newline(self):
        await self.cursor.execute("DROP TABLE test_no_newline IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_no_newline (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)
        
        filepath = os.path.join(self.working_dir, 'test_no_newline.csv')
        self.test_files.append(filepath)
        with open(filepath, 'wb') as f:
            f.write(b'1,Test Data')
        
        await self.cursor.execute(f"""
            INSERT INTO test_no_newline SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_no_newline
            USING (
                ENCODING 'LATIN9'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)
        
        await self.cursor.execute("SELECT COUNT(*) FROM test_no_newline")
        count = (await self.cursor.fetchone())[0]
        
        assert count == 1
    
    @pytest.mark.asyncio
    async def test_import_file_not_found(self):
        await self.cursor.execute("DROP TABLE test_not_found IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_not_found (
                id INT,
                text_data VARCHAR(200)
            ) DISTRIBUTE ON RANDOM
        """)

        non_existent = os.path.join(self.working_dir, 'does_not_exist.csv')

        with pytest.raises((FileNotFoundError, nzpy.Error)):
            await self.cursor.execute(f"""
                INSERT INTO test_not_found SELECT *
                FROM EXTERNAL '{non_existent}' SAMEAS test_not_found
                USING (
                    ENCODING 'LATIN9'
                    REMOTESOURCE 'python'
                    DELIMITER ','
                    LOGDIR '{self.working_dir}'
                )
            """)


class TestExternalTableRoundtrip:
    
    @pytest.fixture(autouse=True)
    def setup(self, con, cursor):
        self.con = con
        self.cursor = cursor
        self.working_dir = tempfile.gettempdir()
        self.test_files = []
        
        yield
        
        for filepath in self.test_files:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
    
    def _track_file(self, filename):
        filepath = os.path.join(self.working_dir, filename)
        self.test_files.append(filepath)
        return filepath

    @pytest.mark.asyncio
    async def test_export_import_dimdate(self):
        """
        Test the roundtrip of exporting a table to disk and then reading it back.
        This simulates saving a table like JUST_DATA..DIMDATE and importing it.
        """
        await self.cursor.execute("DROP TABLE test_dimdate_source IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_dimdate_source (
                date_id INT,
                date_val DATE,
                year INT,
                month INT,
                day INT
            ) DISTRIBUTE ON RANDOM
        """)
        
        await self.cursor.execute("INSERT INTO test_dimdate_source VALUES (1, '2026-05-19', 2026, 5, 19)")
        await self.cursor.execute("INSERT INTO test_dimdate_source VALUES (2, '2026-05-20', 2026, 5, 20)")
        await self.cursor.execute("INSERT INTO test_dimdate_source VALUES (3, '2026-05-21', 2026, 5, 21)")
        
        export_file = 'dimdate_export.csv'
        filepath = self._track_file(export_file)
        
        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{filepath}' USING (
                ENCODING 'UTF8'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            ) AS SELECT * FROM test_dimdate_source
        """)
        
        assert os.path.exists(filepath)
        with open(filepath, 'rb') as f:
            content = f.read()
            assert len(content) > 0
            
        await self.cursor.execute("DROP TABLE test_dimdate_target IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_dimdate_target (
                date_id INT,
                date_val DATE,
                year INT,
                month INT,
                day INT
            ) DISTRIBUTE ON RANDOM
        """)
        
        await self.cursor.execute(f"""
            INSERT INTO test_dimdate_target SELECT *
            FROM EXTERNAL '{filepath}' SAMEAS test_dimdate_target
            USING (
                ENCODING 'UTF8'
                REMOTESOURCE 'python'
                DELIMITER ','
                LOGDIR '{self.working_dir}'
            )
        """)
        
        await self.cursor.execute("SELECT * FROM test_dimdate_target ORDER BY date_id")
        rows = await self.cursor.fetchall()
        
        assert len(rows) == 3
        
        assert rows[0][0] == 1
        assert str(rows[0][1]) == '2026-05-19'
        assert rows[0][2] == 2026
        
        assert rows[2][0] == 3
        assert str(rows[2][1]) == '2026-05-21'


class TestExternalTableCompressed:

    @pytest.fixture(autouse=True)
    def setup(self, con, cursor):
        self.con = con
        self.cursor = cursor
        self.working_dir = tempfile.gettempdir()
        self.test_files = []

        yield

        for filepath in self.test_files:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

    def _track_file(self, filename):
        filepath = os.path.join(self.working_dir, filename)
        self.test_files.append(filepath)
        return filepath

    @pytest.mark.asyncio
    async def test_compressed_internal_export_import(self):
        await self.cursor.execute("DROP TABLE test_comp_src IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_comp_src (
                id INT,
                name VARCHAR(100),
                value FLOAT
            ) DISTRIBUTE ON RANDOM
        """)
        await self.cursor.execute("INSERT INTO test_comp_src VALUES (1, 'alpha', 1.5)")
        await self.cursor.execute("INSERT INTO test_comp_src VALUES (2, 'beta', 2.5)")
        await self.cursor.execute("INSERT INTO test_comp_src VALUES (3, 'gamma', 3.5)")

        export_file = self._track_file('test_comp_internal.nz')

        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{export_file}' USING (
                REMOTESOURCE 'python'
                FORMAT 'INTERNAL'
                COMPRESS 'TRUE'
            ) AS SELECT * FROM test_comp_src
        """)

        assert os.path.exists(export_file)
        assert os.path.getsize(export_file) > 0

        await self.cursor.execute("DROP TABLE test_comp_tgt IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_comp_tgt (
                id INT,
                name VARCHAR(100),
                value FLOAT
            ) DISTRIBUTE ON RANDOM
        """)

        await self.cursor.execute(f"""
            INSERT INTO test_comp_tgt
            SELECT * FROM EXTERNAL '{export_file}'
            USING (
                REMOTESOURCE 'python'
                FORMAT 'INTERNAL'
                COMPRESS 'TRUE'
            )
        """)

        await self.cursor.execute("""
            SELECT COUNT(1) FROM (
                SELECT * FROM test_comp_tgt
                MINUS SELECT * FROM test_comp_src
            ) X
        """)
        diff_count = (await self.cursor.fetchone())[0]
        assert diff_count == 0

    @pytest.mark.asyncio
    async def test_compressed_internal_mixed_types(self):
        await self.cursor.execute("DROP TABLE test_comp_types_src IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_comp_types_src (
                id INT,
                int_val INT,
                big_val BIGINT,
                float_val FLOAT,
                dec_val NUMERIC(10,2),
                str_val VARCHAR(100),
                bool_val BOOLEAN,
                date_val DATE
            ) DISTRIBUTE ON RANDOM
        """)
        await self.cursor.execute(
            "INSERT INTO test_comp_types_src VALUES "
            "(1, 100, 999999, 3.14, 123.45, 'hello', TRUE, '2026-01-15')"
        )
        await self.cursor.execute(
            "INSERT INTO test_comp_types_src VALUES "
            "(2, 200, 888888, 2.71, 67.89, 'world', FALSE, '2026-06-20')"
        )

        export_file = self._track_file('test_comp_types.nz')

        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{export_file}' USING (
                REMOTESOURCE 'python'
                FORMAT 'INTERNAL'
                COMPRESS 'TRUE'
            ) AS SELECT * FROM test_comp_types_src
        """)

        assert os.path.exists(export_file)
        assert os.path.getsize(export_file) > 0

        await self.cursor.execute("DROP TABLE test_comp_types_tgt IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_comp_types_tgt (
                id INT,
                int_val INT,
                big_val BIGINT,
                float_val FLOAT,
                dec_val NUMERIC(10,2),
                str_val VARCHAR(100),
                bool_val BOOLEAN,
                date_val DATE
            ) DISTRIBUTE ON RANDOM
        """)

        await self.cursor.execute(f"""
            INSERT INTO test_comp_types_tgt
            SELECT * FROM EXTERNAL '{export_file}'
            USING (
                REMOTESOURCE 'python'
                FORMAT 'INTERNAL'
                COMPRESS 'TRUE'
            )
        """)

        await self.cursor.execute("SELECT * FROM test_comp_types_tgt ORDER BY id")
        rows = await self.cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 1
        assert rows[0][1] == 100
        assert rows[0][4] == Decimal('123.45')
        assert rows[0][5] == 'hello'
        assert rows[0][6] is True
        assert str(rows[0][7]) == '2026-01-15'

    @pytest.mark.asyncio
    async def test_compressed_internal_larger_roundtrip(self):
        await self.cursor.execute("DROP TABLE test_comp_large_src IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_comp_large_src (
                id INT,
                text_data VARCHAR(500)
            ) DISTRIBUTE ON RANDOM
        """)
        for i in range(15):
            await self.cursor.execute(
                f"INSERT INTO test_comp_large_src VALUES ({i}, "
                f"'Row number {i} with padding text that is repeatable "
                f"and should compress well xxxxxxxxxxxxxxxxxxxxxxxxxxxx')"
            )

        export_file = self._track_file('test_comp_large.nz')

        await self.cursor.execute(f"""
            CREATE EXTERNAL TABLE '{export_file}' USING (
                REMOTESOURCE 'python'
                FORMAT 'INTERNAL'
                COMPRESS 'TRUE'
            ) AS SELECT * FROM test_comp_large_src
        """)

        assert os.path.exists(export_file)
        export_size = os.path.getsize(export_file)
        assert export_size > 0

        await self.cursor.execute("DROP TABLE test_comp_large_tgt IF EXISTS")
        await self.cursor.execute("""
            CREATE TABLE test_comp_large_tgt (
                id INT,
                text_data VARCHAR(500)
            ) DISTRIBUTE ON RANDOM
        """)

        await self.cursor.execute(f"""
            INSERT INTO test_comp_large_tgt
            SELECT * FROM EXTERNAL '{export_file}'
            USING (
                REMOTESOURCE 'python'
                FORMAT 'INTERNAL'
                COMPRESS 'TRUE'
            )
        """)

        await self.cursor.execute("""
            SELECT COUNT(1) FROM (
                SELECT * FROM test_comp_large_tgt
                MINUS SELECT * FROM test_comp_large_src
            ) X
        """)
        diff_count = (await self.cursor.fetchone())[0]
        assert diff_count == 0

        await self.cursor.execute(
            "SELECT COUNT(1) FROM test_comp_large_tgt"
        )
        row_count = (await self.cursor.fetchone())[0]
        assert row_count == 15


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_load_data_streams_large_generator(con):
    """Exercise a multi-megabyte generated import with a real Netezza backend."""
    table = f"PY_LARGE_IMPORT_{uuid.uuid4().hex[:10].upper()}"
    cursor = con.cursor()
    row_count = 4096
    payload = "x" * 512
    rows = ((index, payload) for index in range(row_count))
    try:
        await con.load_data(
            table,
            rows,
            columns=[("id", "INTEGER"), ("text_data", "VARCHAR(512)")],
        )
        await cursor.execute(f"SELECT COUNT(*) FROM {table}")
        assert (await cursor.fetchone())[0] == row_count
    finally:
        await cursor.execute(f"DROP TABLE {table} IF EXISTS")
