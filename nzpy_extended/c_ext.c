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

static PyObject* dec_from_pylong_scaled(PyObject *py_long_val, int scale) {
    if (!DecimalType) {
        Py_INCREF(py_long_val);
        return py_long_val;
    }
    PyObject *dec = PyObject_CallFunctionObjArgs(DecimalType, py_long_val, NULL);
    if (!dec) return NULL;
    if (scale == 0) return dec;

    PyObject *ten = PyLong_FromLong(10);
    PyObject *ten_dec = PyObject_CallFunctionObjArgs(DecimalType, ten, NULL);
    Py_DECREF(ten);
    if (!ten_dec) { Py_DECREF(dec); return NULL; }

    PyObject *neg_scale = PyLong_FromLong(-scale);
    PyObject *scale_pow = PyObject_CallMethod(ten_dec, "__pow__", "(O)", neg_scale);
    Py_DECREF(ten_dec);
    Py_DECREF(neg_scale);
    if (!scale_pow) { Py_DECREF(dec); return NULL; }

    PyObject *result = PyNumber_Multiply(dec, scale_pow);
    Py_DECREF(dec);
    Py_DECREF(scale_pow);
    return result;
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
static PyObject* call_timestamp2struct(int64_t workspace);

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

    const char *data = (const char*)view.buf;
    Py_ssize_t data_len = view.len;

    if (!PyList_Check(py_field_type) || !PyList_Check(py_field_size) ||
        !PyList_Check(py_field_trueSize) || !PyList_Check(py_field_offset) ||
        !PyList_Check(py_field_fixedSize) || !PyList_Check(py_field_physField)) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_TypeError, "field metadata must be lists");
        return NULL;
    }

    int bitmaplen = numFields / 8;
    if (numFields % 8) bitmaplen++;
    if (bitmaplen > data_len) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "data too short for bitmap");
        return NULL;
    }

    int *var_offsets = NULL;
    if (numVaryingFields > 0) {
        var_offsets = (int*)PyMem_Malloc(numVaryingFields * sizeof(int));
        if (!var_offsets) {
            PyBuffer_Release(&view);
            return PyErr_NoMemory();
        }
        int current_voff = fixedFieldsSize;
        for (int i = 0; i < numVaryingFields; i++) {
            var_offsets[i] = current_voff;
            if (current_voff + 2 <= data_len) {
                int vlen = (int)read_u16_le(data + current_voff);
                current_voff += vlen;
                if (vlen % 2 != 0) current_voff += 1;
            }
        }
    }

    /* Pre-extract metadata to C arrays — avoids PyList_GetItem+PyLong_AsLong per field */
    long *c_physField = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_type = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_size = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_trueSize = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_offset = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_fixedSize = (long*)PyMem_Malloc(numFields * sizeof(long));
    if (!c_physField || !c_field_type || !c_field_size || !c_field_trueSize || !c_field_offset || !c_field_fixedSize) {
        PyMem_Free(var_offsets); PyMem_Free(c_physField); PyMem_Free(c_field_type);
        PyMem_Free(c_field_size); PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
        PyBuffer_Release(&view);
        return PyErr_NoMemory();
    }
    for (int i = 0; i < numFields; i++) {
        PyObject *tmp;
        tmp = PyList_GetItem(py_field_physField, i); c_physField[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_type, i);     c_field_type[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_size, i);     c_field_size[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_trueSize, i); c_field_trueSize[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_offset, i);   c_field_offset[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_fixedSize, i); c_field_fixedSize[i] = tmp ? PyLong_AsLong(tmp) : 0;
    }

    PyObject *result = PyList_New(numFields);
    if (!result) {
        PyMem_Free(var_offsets); PyMem_Free(c_physField); PyMem_Free(c_field_type);
        PyMem_Free(c_field_size); PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
        PyBuffer_Release(&view); return PyErr_NoMemory();
    }

    for (int i = 0; i < numFields; i++) {
        long physField = c_physField[i];
        int byte_idx = (int)(physField >> 3);
        int bit_idx = (int)(physField & 7);
        int isNull = 0;
        if (byte_idx < bitmaplen)
            isNull = (data[2 + byte_idx] >> bit_idx) & 1;

        if (isNull && nullsAllowed) {
            PyList_SetItem(result, i, Py_None);
            Py_INCREF(Py_None);
            continue;
        }

        long fldtype    = c_field_type[i];
        long fldsize    = c_field_size[i];
        long fldtrueSz  = c_field_trueSize[i];
        long fldoffset  = c_field_offset[i];
        long fldfixedSz = c_field_fixedSize[i];

        int data_off;
        if (fldfixedSz != 0)
            data_off = (int)fldoffset;
        else if (var_offsets)
            data_off = var_offsets[fldoffset];
        else
            data_off = (int)fldoffset;

        if (data_off < 0 || data_off > (int)data_len) {
            PyList_SetItem(result, i, Py_None);
            Py_INCREF(Py_None);
            continue;
        }

        PyObject *val = NULL;

        if (fldtype == NZ_TYPE_UNKNOWN)
            fldtype = NZ_TYPE_VARCHAR;

        if (fldtype == NZ_TYPE_CHAR) {
            int slen = (int)fldsize;
            if (data_off + slen <= (int)data_len && slen > 0) {
                val = PyUnicode_Decode(data + data_off, slen, char_enc, NULL);
                if (val) {
                    PyObject *trimmed = str_rstrip_null(val);
                    Py_DECREF(val);
                    if (trimmed) {
                        PyObject *padded = str_ljust_spaces(trimmed, slen);
                        Py_DECREF(trimmed);
                        val = padded;
                    } else { Py_INCREF(Py_None); val = Py_None; }
                }
            } else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_NCHAR || fldtype == NZ_TYPE_NVARCHAR) {
            if (data_off + 2 <= (int)data_len) {
                int total_len = (int)read_u16_le(data + data_off);
                int str_len = total_len - 2;
                if (str_len > 0 && data_off + total_len <= (int)data_len) {
                    val = PyUnicode_Decode(data + data_off + 2, str_len, client_enc, NULL);
                    if (val && fldtype == NZ_TYPE_NCHAR) {
                        PyObject *trimmed = str_rstrip_null(val);
                        Py_DECREF(val);
                        if (trimmed) {
                            PyObject *padded = str_ljust_spaces(trimmed, (int)fldsize);
                            Py_DECREF(trimmed);
                            val = padded;
                        } else { Py_INCREF(Py_None); val = Py_None; }
                    }
                } else { Py_INCREF(Py_None); val = Py_None; }
            } else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_VARCHAR || fldtype == NZ_TYPE_VARFIXEDCHAR ||
                 fldtype == NZ_TYPE_GEOMETRY || fldtype == NZ_TYPE_VARBINARY ||
                 fldtype == NZ_TYPE_JSON || fldtype == NZ_TYPE_JSONB ||
                 fldtype == NZ_TYPE_JSONPATH || fldtype == NZ_TYPE_VECTOR) {
            if (data_off + 2 <= (int)data_len) {
                int total_len = (int)read_u16_le(data + data_off);
                int str_len = total_len - 2;
                if (str_len > 0 && data_off + total_len <= (int)data_len)
                    val = PyUnicode_Decode(data + data_off + 2, str_len, char_enc, NULL);
                else { Py_INCREF(Py_None); val = Py_None; }
            } else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_INT8) {
            if (data_off + 8 <= (int)data_len)
                val = PyLong_FromLongLong(read_i64_le(data + data_off));
            else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_INT) {
            if (data_off + 4 <= (int)data_len)
                val = PyLong_FromLong(read_i32_le(data + data_off));
            else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_INT2) {
            if (data_off + 2 <= (int)data_len)
                val = PyLong_FromLong((int16_t)read_u16_le(data + data_off));
            else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_INT1) {
            if (data_off < (int)data_len)
                val = PyLong_FromLong((int8_t)data[data_off]);
            else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_DOUBLE) {
            if (data_off + 8 <= (int)data_len) {
                double dval;
                memcpy(&dval, data + data_off, 8);
                val = PyFloat_FromDouble(dval);
            } else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_FLOAT) {
            if (data_off + 4 <= (int)data_len) {
                float fval;
                memcpy(&fval, data + data_off, 4);
                val = PyFloat_FromDouble((double)fval);
            } else { Py_INCREF(Py_None); val = Py_None; }
        }
        else if (fldtype == NZ_TYPE_DATE) {
                int64_t workspace;
                if (fldsize >= 8 && data_off + 8 <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                } else if (data_off + (int)fldsize <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                    int64_t mask = (1LL << ((int)fldsize * 8)) - 1;
                    if (workspace & (1LL << ((int)fldsize * 8 - 1)))
                        workspace |= ~mask;
                    else
                        workspace &= mask;
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                int jd = (int)(workspace + J2000_OFFSET);
                int y, m, d;
                j2date_c(jd, &y, &m, &d);
                val = PyDate_FromDate(y, m, d);
            }
            else if (fldtype == NZ_TYPE_TIME) {
                int64_t workspace;
                if (fldsize >= 8 && data_off + 8 <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                } else if (data_off + (int)fldsize <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                    int64_t mask = (1LL << ((int)fldsize * 8)) - 1;
                    if (workspace & (1LL << ((int)fldsize * 8 - 1)))
                        workspace |= ~mask;
                    else
                        workspace &= mask;
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                int h, min, s, us;
                time2struct_c(workspace, &h, &min, &s, &us);
                val = PyTime_FromTime(h, min, s, us);
            }
            else if (fldtype == NZ_TYPE_TIMESTAMP) {
                int64_t workspace;
                if (fldsize >= 8 && data_off + 8 <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }

                if (fldsize >= 8) {
                    int64_t date_part = workspace / 86400000000LL;
                    int64_t time_us = workspace % 86400000000LL;
                    if (time_us < 0) { time_us += 86400000000LL; date_part -= 1; }
                    int jd = (int)(date_part + J2000_OFFSET);
                    if (jd < 0) { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                    int y, m, d;
                    j2date_c(jd, &y, &m, &d);
                    int h, min, s, us;
                    time2struct_c(time_us, &h, &min, &s, &us);
                    val = PyDateTime_FromDateAndTime(y, m, d, h, min, s, us);
                } else {
                val = call_timestamp2struct(workspace);
            }
        }
        else if (fldtype == NZ_TYPE_NUMERIC) {
            int chunk_len = (int)fldtrueSz;
            int scale = (int)(fldsize & 0x00FF);
            int count = chunk_len / 4;
            if (count <= 2) {
                val = format_native(read_int64_from_words(data, data_off, count), scale);
            } else {
                val = format_bigint(data, data_off, scale);
            }
        }
        else if (fldtype == NZ_TYPE_BOOL) {
            if (data_off < (int)data_len && data[data_off] == 1) {
                Py_INCREF(Py_True); val = Py_True;
            } else {
                Py_INCREF(Py_False); val = Py_False;
            }
        }
        else if (fldtype == NZ_TYPE_INTERVAL) {
            int64_t interval_time;
            if (fldsize >= 12 && data_off + 12 <= (int)data_len) {
                interval_time = read_i64_le(data + data_off);
            } else if (data_off + (int)fldsize <= (int)data_len) {
                interval_time = read_i64_le(data + data_off);
                int64_t mask = (1LL << (((int)fldsize - 4) * 8)) - 1;
                if (interval_time & (1LL << (((int)fldsize - 4) * 8 - 1)))
                    interval_time |= ~mask;
                else
                    interval_time &= mask;
            } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
            int interval_month = read_i32_le(data + data_off + (int)fldsize - 4);
            val = call_interval_to_text(interval_time, interval_month);
        }
        else if (fldtype == NZ_TYPE_TIMETZ) {
            int64_t timetz_time;
            if (fldsize >= 12 && data_off + 12 <= (int)data_len) {
                timetz_time = read_i64_le(data + data_off);
            } else if (data_off + (int)fldsize <= (int)data_len) {
                timetz_time = read_i64_le(data + data_off);
                int64_t mask = (1LL << (((int)fldsize - 4) * 8)) - 1;
                if (timetz_time & (1LL << (((int)fldsize - 4) * 8 - 1)))
                    timetz_time |= ~mask;
                else
                    timetz_time &= mask;
            } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
            int timetz_zone = read_i32_le(data + data_off + (int)fldsize - 4);
            val = call_timetz_out(timetz_time, timetz_zone);
        }
        else {
            Py_INCREF(Py_None);
            val = Py_None;
        }

    set_val:
        if (val)
            PyList_SetItem(result, i, val);
        else {
            Py_INCREF(Py_None);
            PyList_SetItem(result, i, Py_None);
        }
    }

    PyMem_Free(var_offsets);
    PyMem_Free(c_physField); PyMem_Free(c_field_type);
    PyMem_Free(c_field_size); PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
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

static PyObject* call_timestamp2struct(int64_t workspace) {
    static PyObject *func = NULL;
    if (!func) {
        PyObject *mod = PyImport_ImportModule("nzpy_extended.types");
        if (!mod) { PyErr_Clear(); Py_RETURN_NONE; }
        func = PyObject_GetAttrString(mod, "timestamp2struct");
        Py_DECREF(mod);
        if (!func) { PyErr_Clear(); Py_RETURN_NONE; }
    }
    return PyObject_CallFunction(func, "L", workspace);
}

/* ====== hello ====== */

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

    const char *full_data = (const char*)view.buf;
    Py_ssize_t full_data_len = view.len;

    if (!PyList_Check(py_field_type) || !PyList_Check(py_field_size) ||
        !PyList_Check(py_field_trueSize) || !PyList_Check(py_field_offset) ||
        !PyList_Check(py_field_fixedSize) || !PyList_Check(py_field_physField)) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_TypeError, "field metadata must be lists");
        return NULL;
    }

    /* Pre-extract metadata to C arrays once — avoids PyList_GetItem+PyLong_AsLong per field per row */
    long *c_physField = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_type = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_size = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_trueSize = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_offset = (long*)PyMem_Malloc(numFields * sizeof(long));
    long *c_field_fixedSize = (long*)PyMem_Malloc(numFields * sizeof(long));
    if (!c_physField || !c_field_type || !c_field_size || !c_field_trueSize || !c_field_offset || !c_field_fixedSize) {
        PyMem_Free(c_physField); PyMem_Free(c_field_type); PyMem_Free(c_field_size);
        PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
        PyBuffer_Release(&view);
        return PyErr_NoMemory();
    }
    for (int i = 0; i < numFields; i++) {
        PyObject *tmp;
        tmp = PyList_GetItem(py_field_physField, i); c_physField[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_type, i);     c_field_type[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_size, i);     c_field_size[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_trueSize, i); c_field_trueSize[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_offset, i);   c_field_offset[i] = tmp ? PyLong_AsLong(tmp) : 0;
        tmp = PyList_GetItem(py_field_fixedSize, i); c_field_fixedSize[i] = tmp ? PyLong_AsLong(tmp) : 0;
    }

    int bitmaplen = numFields / 8;
    if (numFields % 8) bitmaplen++;

    PyObject *rows_list = PyList_New(0);
    if (!rows_list) {
        PyMem_Free(c_physField); PyMem_Free(c_field_type); PyMem_Free(c_field_size);
        PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
        PyBuffer_Release(&view);
        return NULL;
    }

    Py_ssize_t bytes_consumed = 0;

    while (bytes_consumed + 13 <= full_data_len) {
        if (full_data[bytes_consumed] != 'Y') {
            break;
        }

        const uint8_t *p = (const uint8_t*)(full_data + bytes_consumed + 9);
        uint32_t tup_len = ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];

        if (bytes_consumed + 13 + tup_len > full_data_len) {
            break;
        }

        const char *data = full_data + bytes_consumed + 13;
        Py_ssize_t data_len = tup_len;

        if (bitmaplen > data_len) {
            PyMem_Free(c_physField); PyMem_Free(c_field_type); PyMem_Free(c_field_size);
            PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
            PyBuffer_Release(&view);
            Py_DECREF(rows_list);
            PyErr_SetString(PyExc_ValueError, "data too short for bitmap");
            return NULL;
        }

        int *var_offsets = NULL;
        if (numVaryingFields > 0) {
            var_offsets = (int*)PyMem_Malloc(numVaryingFields * sizeof(int));
            if (!var_offsets) {
                PyMem_Free(c_physField); PyMem_Free(c_field_type); PyMem_Free(c_field_size);
                PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
                PyBuffer_Release(&view);
                Py_DECREF(rows_list);
                return PyErr_NoMemory();
            }
            int current_voff = fixedFieldsSize;
            for (int i = 0; i < numVaryingFields; i++) {
                var_offsets[i] = current_voff;
                if (current_voff + 2 <= data_len) {
                    int vlen = (int)read_u16_le(data + current_voff);
                    current_voff += vlen;
                    if (vlen % 2 != 0) current_voff += 1;
                }
            }
        }

        PyObject *result = PyList_New(numFields);
        if (!result) {
            PyMem_Free(var_offsets);
            PyMem_Free(c_physField); PyMem_Free(c_field_type); PyMem_Free(c_field_size);
            PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
            PyBuffer_Release(&view);
            Py_DECREF(rows_list);
            return PyErr_NoMemory();
        }

        for (int i = 0; i < numFields; i++) {
            long physField = c_physField[i];
            int byte_idx = (int)(physField >> 3);
            int bit_idx = (int)(physField & 7);
            int isNull = 0;
            if (byte_idx < bitmaplen)
                isNull = (data[2 + byte_idx] >> bit_idx) & 1;

            if (isNull && nullsAllowed) {
                PyList_SetItem(result, i, Py_None);
                Py_INCREF(Py_None);
                continue;
            }

            long fldtype    = c_field_type[i];
            long fldsize    = c_field_size[i];
            long fldtrueSz  = c_field_trueSize[i];
            long fldoffset  = c_field_offset[i];
            long fldfixedSz = c_field_fixedSize[i];

            int data_off;
            if (fldfixedSz != 0)
                data_off = (int)fldoffset;
            else if (var_offsets)
                data_off = var_offsets[fldoffset];
            else
                data_off = (int)fldoffset;

            if (data_off < 0 || data_off > (int)data_len) {
                PyList_SetItem(result, i, Py_None);
                Py_INCREF(Py_None);
                continue;
            }

            PyObject *val = NULL;

            if (fldtype == NZ_TYPE_UNKNOWN)
                fldtype = NZ_TYPE_VARCHAR;

            if (fldtype == NZ_TYPE_CHAR) {
                int slen = (int)fldsize;
                if (data_off + slen <= (int)data_len && slen > 0) {
                    val = PyUnicode_Decode(data + data_off, slen, char_enc, NULL);
                    if (val) {
                        PyObject *trimmed = str_rstrip_null(val);
                        Py_DECREF(val);
                        if (trimmed) {
                            PyObject *padded = str_ljust_spaces(trimmed, slen);
                            Py_DECREF(trimmed);
                            val = padded;
                        } else { Py_INCREF(Py_None); val = Py_None; }
                    }
                } else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_NCHAR || fldtype == NZ_TYPE_NVARCHAR) {
                if (data_off + 2 <= (int)data_len) {
                    int total_len = (int)read_u16_le(data + data_off);
                    int str_len = total_len - 2;
                    if (str_len > 0 && data_off + total_len <= (int)data_len) {
                        val = PyUnicode_Decode(data + data_off + 2, str_len, client_enc, NULL);
                        if (val && fldtype == NZ_TYPE_NCHAR) {
                            PyObject *trimmed = str_rstrip_null(val);
                            Py_DECREF(val);
                            if (trimmed) {
                                PyObject *padded = str_ljust_spaces(trimmed, (int)fldsize);
                                Py_DECREF(trimmed);
                                val = padded;
                            } else { Py_INCREF(Py_None); val = Py_None; }
                        }
                    } else { Py_INCREF(Py_None); val = Py_None; }
                } else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_VARCHAR || fldtype == NZ_TYPE_VARFIXEDCHAR ||
                     fldtype == NZ_TYPE_GEOMETRY || fldtype == NZ_TYPE_VARBINARY ||
                     fldtype == NZ_TYPE_JSON || fldtype == NZ_TYPE_JSONB ||
                     fldtype == NZ_TYPE_JSONPATH || fldtype == NZ_TYPE_VECTOR) {
                if (data_off + 2 <= (int)data_len) {
                    int total_len = (int)read_u16_le(data + data_off);
                    int str_len = total_len - 2;
                    if (str_len > 0 && data_off + total_len <= (int)data_len)
                        val = PyUnicode_Decode(data + data_off + 2, str_len, char_enc, NULL);
                    else { Py_INCREF(Py_None); val = Py_None; }
                } else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_INT8) {
                if (data_off + 8 <= (int)data_len)
                    val = PyLong_FromLongLong(read_i64_le(data + data_off));
                else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_INT) {
                if (data_off + 4 <= (int)data_len)
                    val = PyLong_FromLong(read_i32_le(data + data_off));
                else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_INT2) {
                if (data_off + 2 <= (int)data_len)
                    val = PyLong_FromLong((int16_t)read_u16_le(data + data_off));
                else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_INT1) {
                if (data_off < (int)data_len)
                    val = PyLong_FromLong((int8_t)data[data_off]);
                else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_DOUBLE) {
                if (data_off + 8 <= (int)data_len) {
                    double dval;
                    memcpy(&dval, data + data_off, 8);
                    val = PyFloat_FromDouble(dval);
                } else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_FLOAT) {
                if (data_off + 4 <= (int)data_len) {
                    float fval;
                    memcpy(&fval, data + data_off, 4);
                    val = PyFloat_FromDouble((double)fval);
                } else { Py_INCREF(Py_None); val = Py_None; }
            }
            else if (fldtype == NZ_TYPE_DATE) {
                int64_t workspace;
                if (fldsize >= 8 && data_off + 8 <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                } else if (data_off + (int)fldsize <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                    int64_t mask = (1LL << ((int)fldsize * 8)) - 1;
                    if (workspace & (1LL << ((int)fldsize * 8 - 1)))
                        workspace |= ~mask;
                    else
                        workspace &= mask;
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                int jd = (int)(workspace + J2000_OFFSET);
                int y, m, d;
                j2date_c(jd, &y, &m, &d);
                val = PyDate_FromDate(y, m, d);
            }
            else if (fldtype == NZ_TYPE_TIME) {
                int64_t workspace;
                if (fldsize >= 8 && data_off + 8 <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                } else if (data_off + (int)fldsize <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                    int64_t mask = (1LL << ((int)fldsize * 8)) - 1;
                    if (workspace & (1LL << ((int)fldsize * 8 - 1)))
                        workspace |= ~mask;
                    else
                        workspace &= mask;
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                int h, min, s, us;
                time2struct_c(workspace, &h, &min, &s, &us);
                val = PyTime_FromTime(h, min, s, us);
            }
            else if (fldtype == NZ_TYPE_TIMESTAMP) {
                int64_t workspace;
                if (fldsize >= 8 && data_off + 8 <= (int)data_len) {
                    workspace = read_i64_le(data + data_off);
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }

                if (fldsize >= 8) {
                    int64_t date_part = workspace / 86400000000LL;
                    int64_t time_us = workspace % 86400000000LL;
                    if (time_us < 0) { time_us += 86400000000LL; date_part -= 1; }
                    int jd = (int)(date_part + J2000_OFFSET);
                    if (jd < 0) { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                    int y, m, d;
                    j2date_c(jd, &y, &m, &d);
                    int h, min, s, us;
                    time2struct_c(time_us, &h, &min, &s, &us);
                    val = PyDateTime_FromDateAndTime(y, m, d, h, min, s, us);
                } else {
                    val = call_timestamp2struct(workspace);
                }
            }
            else if (fldtype == NZ_TYPE_NUMERIC) {
                int chunk_len = (int)fldtrueSz;
                int scale = (int)(fldsize & 0x00FF);
                int count = chunk_len / 4;
                if (count <= 2) {
                    val = format_native(read_int64_from_words(data, data_off, count), scale);
                } else {
                    val = format_bigint(data, data_off, scale);
                }
            }
            else if (fldtype == NZ_TYPE_BOOL) {
                if (data_off < (int)data_len && data[data_off] == 1) {
                    Py_INCREF(Py_True); val = Py_True;
                } else {
                    Py_INCREF(Py_False); val = Py_False;
                }
            }
            else if (fldtype == NZ_TYPE_INTERVAL) {
                int64_t interval_time;
                if (fldsize >= 12 && data_off + 12 <= (int)data_len) {
                    interval_time = read_i64_le(data + data_off);
                } else if (data_off + (int)fldsize <= (int)data_len) {
                    interval_time = read_i64_le(data + data_off);
                    int64_t mask = (1LL << (((int)fldsize - 4) * 8)) - 1;
                    if (interval_time & (1LL << (((int)fldsize - 4) * 8 - 1)))
                        interval_time |= ~mask;
                    else
                        interval_time &= mask;
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                int interval_month = read_i32_le(data + data_off + (int)fldsize - 4);
                val = call_interval_to_text(interval_time, interval_month);
            }
            else if (fldtype == NZ_TYPE_TIMETZ) {
                int64_t timetz_time;
                if (fldsize >= 12 && data_off + 12 <= (int)data_len) {
                    timetz_time = read_i64_le(data + data_off);
                } else if (data_off + (int)fldsize <= (int)data_len) {
                    timetz_time = read_i64_le(data + data_off);
                    int64_t mask = (1LL << (((int)fldsize - 4) * 8)) - 1;
                    if (timetz_time & (1LL << (((int)fldsize - 4) * 8 - 1)))
                        timetz_time |= ~mask;
                    else
                        timetz_time &= mask;
                } else { Py_INCREF(Py_None); val = Py_None; goto set_val; }
                int timetz_zone = read_i32_le(data + data_off + (int)fldsize - 4);
                val = call_timetz_out(timetz_time, timetz_zone);
            }
            else {
                Py_INCREF(Py_None);
                val = Py_None;
            }

        set_val:
            if (val)
                PyList_SetItem(result, i, val);
            else {
                Py_INCREF(Py_None);
                PyList_SetItem(result, i, Py_None);
            }
        }

        if (var_offsets) PyMem_Free(var_offsets);
        PyList_Append(rows_list, result);
        Py_DECREF(result);

        bytes_consumed += 13 + tup_len;
    }

    PyMem_Free(c_physField); PyMem_Free(c_field_type); PyMem_Free(c_field_size);
    PyMem_Free(c_field_trueSize); PyMem_Free(c_field_offset); PyMem_Free(c_field_fixedSize);
    PyBuffer_Release(&view);
    PyObject *ret = Py_BuildValue("On", rows_list, bytes_consumed);
    Py_DECREF(rows_list);
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
