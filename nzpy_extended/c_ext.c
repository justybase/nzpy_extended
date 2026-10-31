#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <datetime.h>
#include <stdint.h>
#include <string.h>

/* ====== helpers ====== */

static uint32_t read_u32_le(const char *data) {
    return (uint32_t)(
        (uint8_t)data[0] |
        ((uint8_t)data[1] << 8) |
        ((uint8_t)data[2] << 16) |
        ((uint8_t)data[3] << 24)
    );
}

static int32_t read_i32_le(const char *data) {
    return (int32_t)read_u32_le(data);
}

static int64_t read_i64_le(const char *data) {
    uint64_t lo = read_u32_le(data);
    uint64_t hi = read_u32_le(data + 4);
    return (int64_t)(lo | (hi << 32));
}

#define J2000_OFFSET 2451545

/* cached Decimal type for NUMERIC conversion */
static PyObject *DecimalType = NULL;

/* j2date: convert Julian day number to (year, month, day) */
static void j2date_c(int jd, int *year, int *month, int *day) {
    int l = jd + 68569;
    int n = (4 * l) / 146097;
    l -= (146097 * n + 3) / 4;
    int i = (4000 * (l + 1)) / 1461001;
    l += 31 - (1461 * i) / 4;
    int j = (80 * l) / 2447;
    *day = l - (2447 * j) / 80;
    l = j / 11;
    *month = (j + 2) - (12 * l);
    *year = 100 * (n - 49) + i + l;
}

/* time2struct: decompose microseconds since midnight */
static void time2struct_c(int64_t time_us, int *hour, int *min, int *sec, int *us) {
    *us = (int)(time_us % 1000000);
    time_us /= 1000000;
    *sec = (int)(time_us % 60);
    time_us /= 60;
    *min = (int)(time_us % 60);
    *hour = (int)(time_us / 60);
}

/* ====== inline rstrip(\x00) / ljust (avoids PyObject_CallMethod) ====== */

static PyObject* str_rstrip_null(PyObject *s) {
    Py_ssize_t len = PyUnicode_GetLength(s);
    if (len == 0) { Py_INCREF(s); return s; }
    int kind = PyUnicode_KIND(s);
    void *data = PyUnicode_DATA(s);
    Py_ssize_t new_len = len;
    while (new_len > 0 && PyUnicode_READ(kind, data, new_len - 1) == 0)
        new_len--;
    if (new_len == len) { Py_INCREF(s); return s; }
    PyObject *trimmed = PyUnicode_Substring(s, 0, new_len);
    if (!trimmed) { Py_INCREF(s); return s; }
    return trimmed;
}

static PyObject* str_ljust_spaces(PyObject *s, Py_ssize_t width) {
    Py_ssize_t len = PyUnicode_GetLength(s);
    if (len >= width) { Py_INCREF(s); return s; }
    PyObject *result = PyUnicode_New(width, 127);
    if (!result) { Py_INCREF(s); return s; }
    int k = PyUnicode_KIND(s);
    void *src = PyUnicode_DATA(s);
    int rk = PyUnicode_KIND(result);
    void *rdst = PyUnicode_DATA(result);
    Py_ssize_t copy_len = (len < width) ? len : width;
    for (Py_ssize_t j = 0; j < copy_len; j++)
        PyUnicode_WRITE(rk, rdst, j, PyUnicode_READ(k, src, j));
    for (Py_ssize_t j = copy_len; j < width; j++)
        PyUnicode_WRITE(rk, rdst, j, (Py_UCS4)' ');
    return result;
}

/* ====== string decode (avoids memoryview slice) ====== */

static PyObject* c_decode_str(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset, length;
    const char *encoding;

    if (!PyArg_ParseTuple(args, "y#iis", &data, &data_len, &offset, &length, &encoding))
        return NULL;
    if (offset + length > data_len) {
        PyErr_SetString(PyExc_ValueError, "offset + length exceeds data");
        return NULL;
    }
    return PyUnicode_Decode(data + offset, length, encoding, NULL);
}

static PyObject* c_decode_var_str(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    const char *encoding;

    if (!PyArg_ParseTuple(args, "y#is", &data, &data_len, &offset, &encoding))
        return NULL;
    if (offset + 2 > data_len) return PyUnicode_FromString("");

    int total_len = (int)read_u32_le(data + offset) & 0xFFFF; /* LE uint16 */
    int str_len = total_len - 2;
    if (str_len <= 0) return PyUnicode_FromString("");
    if (offset + total_len > data_len) {
        PyErr_SetString(PyExc_ValueError, "var str length exceeds data");
        return NULL;
    }
    return PyUnicode_Decode(data + offset + 2, str_len, encoding, NULL);
}

/* ====== int parsers (avoid tuple from struct.unpack) ====== */

static PyObject* c_parse_int8(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    if (!PyArg_ParseTuple(args, "y#i", &data, &data_len, &offset))
        return NULL;
    if (offset >= data_len) return PyLong_FromLong(0);
    return PyLong_FromLong((int8_t)data[offset]);
}

static PyObject* c_parse_int16(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    if (!PyArg_ParseTuple(args, "y#i", &data, &data_len, &offset))
        return NULL;
    if (offset + 2 > data_len) return PyLong_FromLong(0);
    return PyLong_FromLong((int16_t)read_u32_le(data + offset));
}

static PyObject* c_parse_int32(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    if (!PyArg_ParseTuple(args, "y#i", &data, &data_len, &offset))
        return NULL;
    if (offset + 4 > data_len) return PyLong_FromLong(0);
    return PyLong_FromLong(read_i32_le(data + offset));
}

static PyObject* c_parse_int64(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    if (!PyArg_ParseTuple(args, "y#i", &data, &data_len, &offset))
        return NULL;
    if (offset + 8 > data_len) return PyLong_FromLong(0);
    return PyLong_FromLongLong(read_i64_le(data + offset));
}

/* ====== float parsers ====== */

static PyObject* c_parse_float32(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    if (!PyArg_ParseTuple(args, "y#i", &data, &data_len, &offset))
        return NULL;
    if (offset + 4 > data_len) return PyFloat_FromDouble(0.0);
    float val;
    memcpy(&val, data + offset, 4);
    return PyFloat_FromDouble((double)val);
}

static PyObject* c_parse_float64(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    if (!PyArg_ParseTuple(args, "y#i", &data, &data_len, &offset))
        return NULL;
    if (offset + 8 > data_len) return PyFloat_FromDouble(0.0);
    double val;
    memcpy(&val, data + offset, 8);
    return PyFloat_FromDouble(val);
}

/* ====== bool parser ====== */

static PyObject* c_parse_bool(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset;
    if (!PyArg_ParseTuple(args, "y#i", &data, &data_len, &offset))
        return NULL;
    if (offset >= data_len) Py_RETURN_FALSE;
    if (data[offset] == 1) Py_RETURN_TRUE;
    Py_RETURN_FALSE;
}

/* ====== date parser (j2date in C) ====== */

static PyObject* c_parse_date(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset, fldlen;
    if (!PyArg_ParseTuple(args, "y#ii", &data, &data_len, &offset, &fldlen))
        return NULL;

    int64_t workspace;
    if (fldlen >= 8) {
        if (offset + 8 > data_len) return PyUnicode_FromString("0001-01-01");
        workspace = read_i64_le(data + offset);
    } else {
        if (offset + fldlen > data_len) return PyUnicode_FromString("0001-01-01");
        workspace = read_i64_le(data + offset);
        int64_t mask = (1LL << (fldlen * 8)) - 1;
        if (workspace & (1LL << (fldlen * 8 - 1)))
            workspace |= ~mask;
        else
            workspace &= mask;
    }

    int jd = (int)(workspace + J2000_OFFSET);
    int y, m, d;
    j2date_c(jd, &y, &m, &d);
    return PyUnicode_FromFormat("%04d-%02d-%02d", y, m, d);
}

/* ====== time parser (time2struct in C) ====== */

static PyObject* c_parse_time(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset, fldlen;
    if (!PyArg_ParseTuple(args, "y#ii", &data, &data_len, &offset, &fldlen))
        return NULL;

    int64_t workspace;
    if (fldlen >= 8) {
        if (offset + 8 > data_len) return PyUnicode_FromString("00:00:00");
        workspace = read_i64_le(data + offset);
    } else {
        if (offset + fldlen > data_len) return PyUnicode_FromString("00:00:00");
        workspace = read_i64_le(data + offset);
        int64_t mask = (1LL << (fldlen * 8)) - 1;
        if (workspace & (1LL << (fldlen * 8 - 1)))
            workspace |= ~mask;
        else
            workspace &= mask;
    }

    int h, m, s, us;
    time2struct_c(workspace, &h, &m, &s, &us);
    if (us)
        return PyUnicode_FromFormat("%02d:%02d:%02d.%06d", h, m, s, us);
    else
        return PyUnicode_FromFormat("%02d:%02d:%02d", h, m, s);
}

/* ====== timestamp parser ====== */

static PyObject* c_parse_timestamp(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset, fldlen;
    if (!PyArg_ParseTuple(args, "y#ii", &data, &data_len, &offset, &fldlen))
        return NULL;

    int64_t workspace;
    if (fldlen >= 8) {
        if (offset + 8 > data_len) return PyUnicode_FromString("0001-01-01 00:00:00.000000");
        workspace = read_i64_le(data + offset);
    } else {
        if (offset + fldlen > data_len) return PyUnicode_FromString("0001-01-01 00:00:00.000000");
        workspace = read_i64_le(data + offset);
        int64_t mask = (1LL << (fldlen * 8)) - 1;
        if (workspace & (1LL << (fldlen * 8 - 1)))
            workspace |= ~mask;
        else
            workspace &= mask;
    }

    if (fldlen != 8) {
        return PyUnicode_FromString("0001-01-01 00:00:00.000000");
    }

    int64_t date = workspace / 86400000000LL;
    int64_t time_us = workspace % 86400000000LL;
    if (time_us < 0) { time_us += 86400000000LL; date -= 1; }

    int jd = (int)(date + J2000_OFFSET);
    if (jd < 0) return PyUnicode_FromString("0001-01-01 00:00:00.000000");

    int y, m, d;
    j2date_c(jd, &y, &m, &d);

    int h, min, s, us;
    time2struct_c(time_us, &h, &min, &s, &us);

    return PyUnicode_FromFormat("%04d-%02d-%02d %02d:%02d:%02d.%06d", y, m, d, h, min, s, us);
}

/* ====== existing format_numeric (128-bit aware) ====== */

static int64_t read_int64_from_words(const char *data, int offset, int count) {
    if (count == 1) {
        return (int64_t)read_i32_le(data + offset);
    }
    uint64_t uval = ((uint64_t)read_u32_le(data + offset) << 32) | read_u32_le(data + offset + 4);
    return (int64_t)uval;
}

/* Exact Decimal for an unscaled integer and its scale, built from text.
 * Arithmetic on Decimal is rounded to the context precision (28 digits by
 * default), but NUMERIC carries up to 38, so the value is never multiplied. */
static PyObject* dec_from_pylong_scaled(PyObject *py_long_val, int scale) {
    if (!DecimalType) {
        Py_INCREF(py_long_val);
        return py_long_val;
    }
    PyObject *digits = PyObject_Str(py_long_val);
    if (!digits) return NULL;
    PyObject *text = PyUnicode_FromFormat("%UE-%d", digits, scale);
    Py_DECREF(digits);
    if (!text) return NULL;
    PyObject *dec = PyObject_CallFunctionObjArgs(DecimalType, text, NULL);
    Py_DECREF(text);
    return dec;
}

static PyObject* Decimal_From_String(PyObject *str_val) {
    if (!DecimalType) return str_val;
    PyObject *dec = PyObject_CallFunctionObjArgs(DecimalType, str_val, NULL);
    Py_DECREF(str_val);
    return dec;
}

static PyObject* format_native(int64_t val, int scale) {
    PyObject *py_val = PyLong_FromLongLong(val);
    if (!py_val) return NULL;
    PyObject *result = dec_from_pylong_scaled(py_val, scale);
    Py_DECREF(py_val);
    return result;
}

static PyObject* format_bigint(const char *data, int offset, int scale) {
    uint32_t words[4];
    for (int i = 0; i < 4; i++)
        words[i] = read_u32_le(data + offset + i * 4);

    PyObject *val = PyLong_FromUnsignedLong(words[0]);
    if (!val) return NULL;
    PyObject *thirty_two = PyLong_FromLong(32);
    if (!thirty_two) { Py_DECREF(val); return NULL; }
    for (int i = 1; i < 4; i++) {
        PyObject *shifted = PyNumber_Lshift(val, thirty_two);
        Py_DECREF(val);
        if (!shifted) { Py_DECREF(thirty_two); return NULL; }
        PyObject *word_obj = PyLong_FromUnsignedLong(words[i]);
        if (!word_obj) { Py_DECREF(shifted); Py_DECREF(thirty_two); return NULL; }
        val = PyNumber_Or(shifted, word_obj);
        Py_DECREF(shifted);
        Py_DECREF(word_obj);
        if (!val) { Py_DECREF(thirty_two); return NULL; }
    }
    Py_DECREF(thirty_two);

    if (words[0] & 0x80000000) {
        PyObject *power = PyLong_FromString("340282366920938463463374607431768211456", NULL, 10);
        if (!power) { Py_DECREF(val); return NULL; }
        PyObject *temp = PyNumber_Subtract(val, power);
        Py_DECREF(val); Py_DECREF(power);
        val = temp;
        if (!val) return NULL;
    }

    PyObject *result = dec_from_pylong_scaled(val, scale);
    Py_DECREF(val);
    return result;
}

static PyObject* c_format_numeric(PyObject *self, PyObject *args) {
    const char *data; Py_ssize_t data_len;
    int offset, chunk_len, scale;
    if (!PyArg_ParseTuple(args, "y#iii", &data, &data_len, &offset, &chunk_len, &scale))
        return NULL;
    if (offset + chunk_len > data_len) {
        PyErr_SetString(PyExc_ValueError, "offset + chunk_len exceeds data");
        return NULL;
    }
    int count = chunk_len / 4;
    if (count <= 2) return format_native(read_int64_from_words(data, offset, count), scale);
    return format_bigint(data, offset, scale);
}

/* ====== u16 LE helper ====== */

static uint16_t read_u16_le(const char *data) {
    return (uint16_t)(
        (uint8_t)data[0] |
        ((uint8_t)data[1] << 8)
    );
}

/* ====== DBOS row processor (batch parse entire row in C) ====== */

#define NZ_TYPE_RECADDR    1
#define NZ_TYPE_DOUBLE     2
#define NZ_TYPE_INT        3
#define NZ_TYPE_FLOAT      4
#define NZ_TYPE_MONEY      5
#define NZ_TYPE_DATE       6
#define NZ_TYPE_NUMERIC    7
#define NZ_TYPE_TIME       8
#define NZ_TYPE_TIMESTAMP  9
#define NZ_TYPE_INTERVAL  10
#define NZ_TYPE_TIMETZ    11
#define NZ_TYPE_BOOL      12
#define NZ_TYPE_INT1      13
#define NZ_TYPE_BINARY    14
#define NZ_TYPE_CHAR      15
#define NZ_TYPE_VARCHAR   16
#define NZ_TYPE_UNKNOWN   18
#define NZ_TYPE_INT2      19
#define NZ_TYPE_INT8      20
#define NZ_TYPE_VARFIXEDCHAR 21
#define NZ_TYPE_GEOMETRY  22
#define NZ_TYPE_VARBINARY 23
#define NZ_TYPE_NCHAR     25
#define NZ_TYPE_NVARCHAR  26
#define NZ_TYPE_JSON      30
#define NZ_TYPE_JSONB     31
#define NZ_TYPE_JSONPATH  32
#define NZ_TYPE_VECTOR    33

static PyObject* call_interval_to_text(int64_t interval_time, int interval_month);
static PyObject* call_timetz_out(int64_t timetz_time, int timetz_zone);

/* ====== DBOS row decoding ======
 *
 * One decoder serves the single-row and batch entry points. Layout checks run
 * before any value is read and mirror DbosParser._validate_row_layout in
 * _dbos.py. Layout faults raise BufferError, which tells the caller the
 * connection must be retired. Value faults keep the codec's own exception
 * (for example UnicodeDecodeError), which the caller can defer.
 */

typedef struct {
    int numFields;
    int nullsAllowed;
    int fixedFieldsSize;
    int numVaryingFields;
    int bitmaplen;
    const char *char_enc;
    const char *client_enc;
    long *physField, *fldtype, *fldsize, *fldtrueSz, *fldoffset, *fldfixedSz;
    Py_ssize_t *starts;       /* per field: data offset, or -1 for NULL */
    Py_ssize_t *var_offsets;  /* per varying field */
    long *longs;              /* owns the six metadata arrays */
    Py_ssize_t *sizes;        /* owns starts and var_offsets */
} DbosMeta;

static int dbos_fail_layout(const char *msg) {
    PyErr_Format(PyExc_BufferError,
                 "Invalid RowStandard payload: %s; reconnect is required.", msg);
    return -1;
}

static void dbos_meta_free(DbosMeta *m) {
    PyMem_Free(m->longs);
    PyMem_Free(m->sizes);
    m->longs = NULL;
    m->sizes = NULL;
}

/* Copy the column metadata into C arrays once per call, not once per row. */
static int dbos_meta_load(DbosMeta *m, int numFields, int nullsAllowed,
                          int fixedFieldsSize, int numVaryingFields,
                          const char *char_enc, const char *client_enc,
                          PyObject *const *lists) {
    memset(m, 0, sizeof(*m));
    if (numFields < 0 || numVaryingFields < 0 || fixedFieldsSize < 0)
        return dbos_fail_layout("field header is invalid");

    for (int k = 0; k < 6; k++) {
        if (!PyList_Check(lists[k])) {
            PyErr_SetString(PyExc_TypeError, "field metadata must be lists");
            return -1;
        }
        if (PyList_GET_SIZE(lists[k]) < numFields)
            return dbos_fail_layout("field metadata is truncated");
    }

    m->numFields = numFields;
    m->nullsAllowed = nullsAllowed;
    m->fixedFieldsSize = fixedFieldsSize;
    m->numVaryingFields = numVaryingFields;
    m->bitmaplen = (numFields + 7) / 8;
    m->char_enc = char_enc;
    m->client_enc = client_enc;

    size_t stride = (size_t)numFields + 1;
    m->longs = (long*)PyMem_Calloc(6 * stride, sizeof(long));
    m->sizes = (Py_ssize_t*)PyMem_Calloc(stride + (size_t)numVaryingFields + 1,
                                         sizeof(Py_ssize_t));
    if (!m->longs || !m->sizes) {
        PyErr_NoMemory();
        dbos_meta_free(m);
        return -1;
    }
    m->physField = m->longs;
    m->fldtype = m->longs + stride;
    m->fldsize = m->longs + 2 * stride;
    m->fldtrueSz = m->longs + 3 * stride;
    m->fldoffset = m->longs + 4 * stride;
    m->fldfixedSz = m->longs + 5 * stride;
    m->starts = m->sizes;
    m->var_offsets = m->sizes + stride;

    long *columns[6] = {m->physField, m->fldtype, m->fldsize,
                        m->fldtrueSz, m->fldoffset, m->fldfixedSz};
    for (int k = 0; k < 6; k++) {
        for (int i = 0; i < numFields; i++) {
            long v = PyLong_AsLong(PyList_GET_ITEM(lists[k], i));
            if (v == -1 && PyErr_Occurred()) {
                dbos_meta_free(m);
                return -1;
            }
            columns[k][i] = v;
        }
    }
    return 0;
}

/* Bytes that field i's storage must span, bounds-checked against the row.
 * Mirrors DbosParser._validate_field_span. */
static int dbos_check_span(const DbosMeta *m, int i, const char *data,
                           Py_ssize_t data_len, Py_ssize_t start) {
    long fldtype = m->fldtype[i];
    long fldsize = m->fldsize[i];
    Py_ssize_t need = 0;

    if (fldtype == NZ_TYPE_UNKNOWN)
        fldtype = NZ_TYPE_VARCHAR;

    switch (fldtype) {
    case NZ_TYPE_CHAR:
        if (fldsize < 0)
            return dbos_fail_layout("field size is invalid");
        need = fldsize;
        break;
    case NZ_TYPE_NCHAR:
    case NZ_TYPE_NVARCHAR:
    case NZ_TYPE_VARCHAR:
    case NZ_TYPE_VARFIXEDCHAR:
    case NZ_TYPE_GEOMETRY:
    case NZ_TYPE_VARBINARY:
    case NZ_TYPE_JSON:
    case NZ_TYPE_JSONB:
    case NZ_TYPE_JSONPATH:
    case NZ_TYPE_VECTOR: {
        if (start + 2 > data_len)
            return dbos_fail_layout("field length prefix is truncated");
        Py_ssize_t total = (Py_ssize_t)read_u16_le(data + start);
        if (total < 2 || start + total > data_len)
            return dbos_fail_layout("field extends beyond the row");
        return 0;
    }
    case NZ_TYPE_INT8:
    case NZ_TYPE_DOUBLE:
        need = 8;
        break;
    case NZ_TYPE_INT:
    case NZ_TYPE_FLOAT:
        need = 4;
        break;
    case NZ_TYPE_INT2:
        need = 2;
        break;
    case NZ_TYPE_INT1:
    case NZ_TYPE_BOOL:
        need = 1;
        break;
    case NZ_TYPE_DATE:
    case NZ_TYPE_TIME:
    case NZ_TYPE_TIMESTAMP:
        if (fldsize < 0)
            return dbos_fail_layout("field size is invalid");
        need = fldsize >= 8 ? 8 : fldsize;
        break;
    case NZ_TYPE_INTERVAL:
    case NZ_TYPE_TIMETZ:
        if (fldsize < 4)
            return dbos_fail_layout("field size is invalid");
        need = fldsize;
        break;
    case NZ_TYPE_NUMERIC: {
        long chunk = m->fldtrueSz[i];
        if (chunk < 4 || chunk > 16 || chunk % 4 != 0)
            return dbos_fail_layout("numeric width is invalid");
        need = chunk;
        break;
    }
    default:
        break;
    }

    if (start + need > data_len)
        return dbos_fail_layout("field extends beyond the row");
    return 0;
}

/* Resolve every field's start and bounds-check it before decoding any value.
 * Mirrors DbosParser._validate_row_layout. */
static int dbos_check_layout(DbosMeta *m, const char *data, Py_ssize_t data_len) {
    if (2 + (Py_ssize_t)m->bitmaplen > data_len)
        return dbos_fail_layout("row is too short for the NULL bitmap");

    Py_ssize_t cur = m->fixedFieldsSize;
    for (int v = 0; v < m->numVaryingFields; v++) {
        m->var_offsets[v] = cur;
        if (cur + 2 > data_len)
            return dbos_fail_layout("varying field length is truncated");
        Py_ssize_t vlen = (Py_ssize_t)read_u16_le(data + cur);
        if (vlen < 2)
            return dbos_fail_layout("varying field length is invalid");
        Py_ssize_t next = cur + vlen + (vlen & 1);
        if (next > data_len)
            return dbos_fail_layout("varying field extends beyond the row");
        cur = next;
    }

    for (int i = 0; i < m->numFields; i++) {
        long phys = m->physField[i];
        if (m->nullsAllowed != 0) {
            if (phys < 0 || phys >= m->numFields)
                return dbos_fail_layout("field physical index is invalid");
            if ((((unsigned char)data[2 + (phys >> 3)]) >> (phys & 7)) & 1) {
                m->starts[i] = -1;
                continue;
            }
        }

        long fixed = m->fldfixedSz[i];
        Py_ssize_t start;
        if (fixed != 0) {
            if (fixed < 0)
                return dbos_fail_layout("fixed field size is invalid");
            start = (Py_ssize_t)m->fldoffset[i];
        } else if (m->numVaryingFields > 0) {
            long vidx = m->fldoffset[i];
            if (vidx < 0 || vidx >= m->numVaryingFields)
                return dbos_fail_layout("varying field index is invalid");
            start = m->var_offsets[vidx];
        } else {
            start = m->fixedFieldsSize;
        }
        if (start < 0 || start > data_len)
            return dbos_fail_layout("field starts outside the row");
        if (dbos_check_span(m, i, data, data_len, start) < 0)
            return -1;
        m->starts[i] = start;
    }
    return 0;
}

/* Read n (<= 8) little-endian bytes as a signed value, sign-extending from
 * the top bit. Matches the partial-width reads used for DATE/TIME fields. */
static int64_t read_signed_le_n(const char *p, Py_ssize_t n) {
    if (n >= 8)
        return read_i64_le(p);
    if (n <= 0)
        return 0;
    uint64_t u = 0;
    for (Py_ssize_t k = 0; k < n; k++)
        u |= (uint64_t)(uint8_t)p[k] << (8 * k);
    uint64_t sign = (uint64_t)1 << (8 * n - 1);
    if (u & sign)
        u |= ~((sign << 1) - 1);
    return (int64_t)u;
}

/* CHAR/NCHAR storage: strip NUL padding, then pad with spaces to the width. */
static PyObject* dbos_pad_text(PyObject *val, Py_ssize_t width) {
    if (!val)
        return NULL;
    PyObject *trimmed = str_rstrip_null(val);
    Py_DECREF(val);
    if (!trimmed)
        return NULL;
    PyObject *padded = str_ljust_spaces(trimmed, width);
    Py_DECREF(trimmed);
    return padded;
}

/* Decode one validated field. Returns a new reference, or NULL with an
 * exception set. */
static PyObject* dbos_decode_field(const DbosMeta *m, int i, const char *data) {
    Py_ssize_t off = m->starts[i];
    if (off < 0)
        Py_RETURN_NONE;
    const char *p = data + off;
    long fldtype = m->fldtype[i];
    long fldsize = m->fldsize[i];

    if (fldtype == NZ_TYPE_UNKNOWN)
        fldtype = NZ_TYPE_VARCHAR;

    switch (fldtype) {
    case NZ_TYPE_CHAR:
        if (fldsize <= 0)
            Py_RETURN_NONE;
        return dbos_pad_text(
            PyUnicode_Decode(p, (Py_ssize_t)fldsize, m->char_enc, NULL),
            (Py_ssize_t)fldsize);

    case NZ_TYPE_NCHAR:
    case NZ_TYPE_NVARCHAR: {
        /* A zero length is an empty string; NULL comes from the bitmap. */
        Py_ssize_t str_len = (Py_ssize_t)read_u16_le(p) - 2;
        if (str_len < 0)
            str_len = 0;
        PyObject *val = PyUnicode_Decode(p + 2, str_len, m->client_enc, NULL);
        if (fldtype == NZ_TYPE_NCHAR)
            return dbos_pad_text(val, (Py_ssize_t)fldsize);
        return val;
    }

    case NZ_TYPE_VARCHAR:
    case NZ_TYPE_VARFIXEDCHAR:
    case NZ_TYPE_GEOMETRY:
    case NZ_TYPE_VARBINARY:
    case NZ_TYPE_JSON:
    case NZ_TYPE_JSONB:
    case NZ_TYPE_JSONPATH:
    case NZ_TYPE_VECTOR: {
        Py_ssize_t str_len = (Py_ssize_t)read_u16_le(p) - 2;
        if (str_len < 0)
            str_len = 0;
        return PyUnicode_Decode(p + 2, str_len, m->char_enc, NULL);
    }

    case NZ_TYPE_INT8:
        return PyLong_FromLongLong(read_i64_le(p));
    case NZ_TYPE_INT:
        return PyLong_FromLong(read_i32_le(p));
    case NZ_TYPE_INT2:
        return PyLong_FromLong((int16_t)read_u16_le(p));
    case NZ_TYPE_INT1:
        return PyLong_FromLong((int8_t)p[0]);

    case NZ_TYPE_DOUBLE: {
        double dval;
        memcpy(&dval, p, 8);
        return PyFloat_FromDouble(dval);
    }
    case NZ_TYPE_FLOAT: {
        float fval;
        memcpy(&fval, p, 4);
        return PyFloat_FromDouble((double)fval);
    }

    case NZ_TYPE_DATE: {
        int64_t workspace = read_signed_le_n(p, fldsize >= 8 ? 8 : fldsize);
        int y, mo, d;
        j2date_c((int)(workspace + J2000_OFFSET), &y, &mo, &d);
        return PyDate_FromDate(y, mo, d);
    }
    case NZ_TYPE_TIME: {
        int64_t workspace = read_signed_le_n(p, fldsize >= 8 ? 8 : fldsize);
        int h, min, s, us;
        time2struct_c(workspace, &h, &min, &s, &us);
        return PyTime_FromTime(h, min, s, us);
    }
    case NZ_TYPE_TIMESTAMP: {
        if (fldsize < 8)
            Py_RETURN_NONE;
        int64_t workspace = read_i64_le(p);
        int64_t date_part = workspace / 86400000000LL;
        int64_t time_us = workspace % 86400000000LL;
        if (time_us < 0) {
            time_us += 86400000000LL;
            date_part -= 1;
        }
        int jd = (int)(date_part + J2000_OFFSET);
        if (jd < 0)
            Py_RETURN_NONE;
        int y, mo, d, h, min, s, us;
        j2date_c(jd, &y, &mo, &d);
        time2struct_c(time_us, &h, &min, &s, &us);
        return PyDateTime_FromDateAndTime(y, mo, d, h, min, s, us);
    }

    case NZ_TYPE_NUMERIC: {
        int chunk_len = (int)m->fldtrueSz[i];
        int scale = (int)(fldsize & 0x00FF);
        int count = chunk_len / 4;
        if (count <= 2)
            return format_native(read_int64_from_words(data, (int)off, count), scale);
        return format_bigint(data, (int)off, scale);
    }

    case NZ_TYPE_BOOL:
        if (p[0] == 1)
            Py_RETURN_TRUE;
        Py_RETURN_FALSE;

    case NZ_TYPE_INTERVAL: {
        int64_t interval_time = read_signed_le_n(p, fldsize >= 12 ? 8 : fldsize - 4);
        int interval_month = read_i32_le(p + fldsize - 4);
        return call_interval_to_text(interval_time, interval_month);
    }
    case NZ_TYPE_TIMETZ: {
        int64_t timetz_time = read_signed_le_n(p, fldsize >= 12 ? 8 : fldsize - 4);
        int timetz_zone = read_i32_le(p + fldsize - 4);
        return call_timetz_out(timetz_time, timetz_zone);
    }

    default:
        Py_RETURN_NONE;
    }
}

static PyObject* dbos_decode_row(DbosMeta *m, const char *data, Py_ssize_t data_len) {
    if (dbos_check_layout(m, data, data_len) < 0)
        return NULL;
    PyObject *row = PyList_New(m->numFields);
    if (!row)
        return NULL;
    for (int i = 0; i < m->numFields; i++) {
        PyObject *val = dbos_decode_field(m, i, data);
        if (!val) {
            Py_DECREF(row);
            return NULL;
        }
        PyList_SET_ITEM(row, i, val);
    }
    return row;
}

static PyObject* c_process_dbos_row(PyObject *self, PyObject *args) {
    Py_buffer view;
    PyObject *py_field_type, *py_field_size, *py_field_trueSize;
    PyObject *py_field_offset, *py_field_fixedSize, *py_field_physField;
    int numFields, nullsAllowed, fixedFieldsSize, numVaryingFields;
    const char *char_enc, *client_enc;

    if (!PyArg_ParseTuple(args, "y*OOOOOOiiiiss",
        &view,
        &py_field_type, &py_field_size, &py_field_trueSize,
        &py_field_offset, &py_field_fixedSize, &py_field_physField,
        &numFields, &nullsAllowed, &fixedFieldsSize, &numVaryingFields,
        &char_enc, &client_enc))
        return NULL;

    PyObject *lists[6] = {py_field_physField, py_field_type, py_field_size,
                          py_field_trueSize, py_field_offset, py_field_fixedSize};
    DbosMeta meta;
    PyObject *result = NULL;
    if (dbos_meta_load(&meta, numFields, nullsAllowed, fixedFieldsSize,
                       numVaryingFields, char_enc, client_enc, lists) == 0)
        result = dbos_decode_row(&meta, (const char*)view.buf, view.len);
    dbos_meta_free(&meta);
    PyBuffer_Release(&view);
    return result;
}

static PyObject* call_interval_to_text(int64_t interval_time, int interval_month) {
    static PyObject *cls = NULL;
    if (!cls) {
        PyObject *mod = PyImport_ImportModule("nzpy_extended.core");
        if (!mod) { PyErr_Clear(); Py_RETURN_NONE; }
        cls = PyObject_GetAttrString(mod, "Interval");
        Py_DECREF(mod);
        if (!cls) { PyErr_Clear(); Py_RETURN_NONE; }
    }
    return PyObject_CallFunction(cls, "Lii", interval_time, 0, interval_month);
}

static PyObject* call_timetz_out(int64_t timetz_time, int timetz_zone) {
    static PyObject *func = NULL;
    if (!func) {
        PyObject *mod = PyImport_ImportModule("nzpy_extended.types");
        if (!mod) { PyErr_Clear(); Py_RETURN_NONE; }
        func = PyObject_GetAttrString(mod, "timetz_out_timetzadt");
        Py_DECREF(mod);
        if (!func) { PyErr_Clear(); Py_RETURN_NONE; }
    }
    return PyObject_CallFunction(func, "Li", timetz_time, timetz_zone);
}

/* Decode consecutive 'Y' frames into rows. Stops at a non-row message or an
 * incomplete frame, which stays buffered for the next read. Returns the bytes
 * consumed, or -1 with an exception set (the caller re-reads row by row). */
static Py_ssize_t dbos_decode_frames(DbosMeta *m, PyObject *rows,
                                     const char *full, Py_ssize_t full_len) {
    Py_ssize_t pos = 0;
    while (pos + 13 <= full_len && full[pos] == 'Y') {
        const uint8_t *p = (const uint8_t*)(full + pos + 9);
        uint32_t tup_len = ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
                           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
        if ((Py_ssize_t)tup_len > full_len - pos - 13)
            break;

        PyObject *row = dbos_decode_row(m, full + pos + 13, (Py_ssize_t)tup_len);
        if (!row)
            return -1;
        int rc = PyList_Append(rows, row);
        Py_DECREF(row);
        if (rc < 0)
            return -1;
        pos += 13 + (Py_ssize_t)tup_len;
    }
    return pos;
}

static PyObject* c_process_dbos_batch(PyObject *self, PyObject *args) {
    Py_buffer view;
    PyObject *py_field_type, *py_field_size, *py_field_trueSize;
    PyObject *py_field_offset, *py_field_fixedSize, *py_field_physField;
    int numFields, nullsAllowed, fixedFieldsSize, numVaryingFields;
    const char *char_enc, *client_enc;

    if (!PyArg_ParseTuple(args, "y*OOOOOOiiiiss",
        &view,
        &py_field_type, &py_field_size, &py_field_trueSize,
        &py_field_offset, &py_field_fixedSize, &py_field_physField,
        &numFields, &nullsAllowed, &fixedFieldsSize, &numVaryingFields,
        &char_enc, &client_enc))
        return NULL;

    PyObject *lists[6] = {py_field_physField, py_field_type, py_field_size,
                          py_field_trueSize, py_field_offset, py_field_fixedSize};
    DbosMeta meta;
    PyObject *rows_list = NULL;
    PyObject *ret = NULL;
    Py_ssize_t consumed = -1;

    if (dbos_meta_load(&meta, numFields, nullsAllowed, fixedFieldsSize,
                       numVaryingFields, char_enc, client_enc, lists) == 0) {
        rows_list = PyList_New(0);
        if (rows_list)
            consumed = dbos_decode_frames(&meta, rows_list,
                                          (const char*)view.buf, view.len);
    }
    if (consumed >= 0)
        ret = Py_BuildValue("On", rows_list, consumed);
    Py_XDECREF(rows_list);
    dbos_meta_free(&meta);
    PyBuffer_Release(&view);
    return ret;
}

static PyObject* c_hello(PyObject *self, PyObject *args) {
    return PyUnicode_FromString("Hello from C extension!");
}

static PyMethodDef CExtMethods[] = {
    {"hello", c_hello, METH_VARARGS, NULL},
    {"decode_str", c_decode_str, METH_VARARGS, NULL},
    {"decode_var_str", c_decode_var_str, METH_VARARGS, NULL},
    {"parse_int8", c_parse_int8, METH_VARARGS, NULL},
    {"parse_int16", c_parse_int16, METH_VARARGS, NULL},
    {"parse_int32", c_parse_int32, METH_VARARGS, NULL},
    {"parse_int64", c_parse_int64, METH_VARARGS, NULL},
    {"parse_float32", c_parse_float32, METH_VARARGS, NULL},
    {"parse_float64", c_parse_float64, METH_VARARGS, NULL},
    {"parse_bool", c_parse_bool, METH_VARARGS, NULL},
    {"parse_date", c_parse_date, METH_VARARGS, NULL},
    {"parse_time", c_parse_time, METH_VARARGS, NULL},
    {"parse_timestamp", c_parse_timestamp, METH_VARARGS, NULL},
    {"format_numeric", c_format_numeric, METH_VARARGS, NULL},
    {"process_dbos_row", c_process_dbos_row, METH_VARARGS, NULL},
    {"process_dbos_batch", c_process_dbos_batch, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef c_ext_module = {
    PyModuleDef_HEAD_INIT, "c_ext", NULL, -1, CExtMethods
};

PyMODINIT_FUNC PyInit_c_ext(void) {
    PyObject *m = PyModule_Create(&c_ext_module);
    if (!m) return NULL;

    PyDateTime_IMPORT;

    PyObject *decimal_mod = PyImport_ImportModule("decimal");
    if (decimal_mod) {
        DecimalType = PyObject_GetAttrString(decimal_mod, "Decimal");
        Py_DECREF(decimal_mod);
    }

    return m;
}
