/*
 * JSON-lines bridge for the independent JustyBase Netezza driver.
 *
 * The Python parity tests deliberately run this process separately so the
 * reference implementation cannot share nzpy_extended's protocol code.
 */

const path = require('node:path');
const readline = require('node:readline');

function resolveDriverRoot() {
    if (process.env.NZ_REFERENCE_NODE_DRIVER) {
        return process.env.NZ_REFERENCE_NODE_DRIVER;
    }
    return path.resolve(__dirname, '../../justybase_netezza_node_driver');
}

const driverRoot = resolveDriverRoot();
const { NzConnection } = require(path.join(driverRoot, 'dist', 'cjs'));

const config = {
    host: process.env.NZ_DEV_HOST,
    port: Number(process.env.NZ_DEV_PORT || '5480'),
    database: process.env.NZ_DEV_DATABASE || process.env.NZ_DEV_DB,
    user: process.env.NZ_DEV_USER,
    password: process.env.NZ_DEV_PASSWORD,
};

function isoDate(value) {
    return value.toISOString().slice(0, 10);
}

function isoTimestamp(value) {
    return value.toISOString().replace('T', ' ').replace('Z', '');
}

function serializeValue(value, oid) {
    if (value === null) return { kind: 'null' };
    if (value === undefined) return { kind: 'undefined' };
    if (typeof value === 'boolean') return { kind: 'bool', value };
    if (typeof value === 'bigint') return { kind: 'int', value: value.toString() };
    if (Buffer.isBuffer(value)) {
        return { kind: 'bytes', value: value.toString('base64') };
    }
    if (value instanceof Date) {
        return {
            kind: oid === 1082 ? 'date' : 'datetime',
            value: oid === 1082 ? isoDate(value) : isoTimestamp(value),
        };
    }
    if (typeof value === 'number') {
        if (oid === 20 || oid === 21 || oid === 23 || oid === 26 || oid === 2500) {
            return { kind: 'int', value: String(value) };
        }
        if (oid === 700 || oid === 701) {
            return { kind: 'float', value: String(value) };
        }
        if (oid === 1700) {
            return { kind: 'numeric', value: String(value) };
        }
        return { kind: 'number', value: String(value) };
    }
    if (typeof value === 'object' &&
        Number.isInteger(value.hours) && Number.isInteger(value.minutes) &&
        Number.isInteger(value.seconds) && Number.isInteger(value.microseconds)) {
        const fraction = value.microseconds ? `.${String(value.microseconds).padStart(6, '0')}` : '';
        return {
            kind: 'time',
            value: `${String(value.hours).padStart(2, '0')}:${String(value.minutes).padStart(2, '0')}:${String(value.seconds).padStart(2, '0')}${fraction}`,
        };
    }
    if (typeof value === 'string') {
        return { kind: oid === 1700 ? 'numeric' : 'string', value };
    }
    return { kind: 'string', value: String(value) };
}

let connection;
let queue = Promise.resolve();

async function execute(request) {
    if (!connection) {
        connection = new NzConnection(config);
        await connection.connect();
    }

    const command = connection.createCommand(request.sql);
    const reader = await command.executeReader();
    try {
        const fields = reader.columnDescriptions.map((field) => ({
            name: field.name,
            oid: field.typeOid,
            typeModifier: field.typeMod,
        }));
        const rows = [];
        while (await reader.read()) {
            const values = reader.getValues();
            rows.push(values.map((value, index) => serializeValue(value, fields[index]?.oid)));
        }
        return { ok: true, fields, rows };
    } finally {
        await reader.close();
    }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', (line) => {
    queue = queue.then(async () => {
        try {
            const result = await execute(JSON.parse(line));
            process.stdout.write(`${JSON.stringify(result)}\n`);
        } catch (error) {
            process.stdout.write(`${JSON.stringify({
                ok: false,
                error: error instanceof Error ? error.message : String(error),
            })}\n`);
        }
    });
});

input.on('close', async () => {
    await queue;
    if (connection) connection.close();
});
