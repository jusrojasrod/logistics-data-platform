// Usuario de aplicación de la vía API y colecciones de aterrizaje.
// Se ejecuta en el volumen vacío desde el entrypoint de la imagen y, en un
// volumen ya creado, desde scripts/mongo_app_user.sh. Es idempotente.

const APP_USER = "nexora_app";
const APP_PASSWORD = process.env.MONGO_APP_PASSWORD;

if (!APP_PASSWORD) {
  throw new Error("MONGO_APP_PASSWORD no definido: no se puede crear el usuario de aplicación");
}

const adminDb = db.getSiblingDB("admin");
const appRoles = [
  { role: "readWrite", db: "raw" },
  { role: "readWrite", db: "meta" },
];

if (adminDb.getUser(APP_USER) === null) {
  adminDb.createUser({ user: APP_USER, pwd: APP_PASSWORD, roles: appRoles });
  print(`[mongo-init] creado ${APP_USER} con readWrite en raw y meta`);
} else {
  adminDb.updateUser(APP_USER, { roles: appRoles });
  print(`[mongo-init] ${APP_USER} ya existe: roles ajustados a raw y meta`);
}

// Los seis metadatos son obligatorios y no nulos en ambas colecciones.
// Los campos propios de cada colección (payload, field, rule_id, ...) se
// admiten sin restricción porque el aterrizaje guarda el JSON sin transformar.
const metadataValidator = {
  $jsonSchema: {
    bsonType: "object",
    required: [
      "run_id",
      "source_system",
      "endpoint",
      "page",
      "payload_sha256",
      "ingested_at",
    ],
    properties: {
      run_id: { bsonType: "string" },
      source_system: { bsonType: "string" },
      endpoint: { bsonType: "string" },
      page: { bsonType: ["int", "long"] },
      payload_sha256: { bsonType: "string" },
      ingested_at: { bsonType: "date" },
    },
  },
};

function ensureCollection(dbName, collectionName) {
  const target = db.getSiblingDB(dbName);
  if (!target.getCollectionNames().includes(collectionName)) {
    target.createCollection(collectionName, {
      validator: metadataValidator,
      validationLevel: "strict",
      validationAction: "error",
    });
    print(`[mongo-init] creada ${dbName}.${collectionName} con validador de metadatos`);
  } else {
    print(`[mongo-init] ${dbName}.${collectionName} ya existe: sin cambios`);
  }
  return target.getCollection(collectionName);
}

function sameKeys(left, right) {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every((key) => key in right && left[key] === right[key])
  );
}

function ensureIndex(collection, keys, options) {
  const existing = collection.getIndexes().find((index) => sameKeys(index.key, keys));
  if (!existing) {
    collection.createIndex(keys, options);
  }
  print(`[mongo-init] índice ${options.name} presente en ${collection.getFullName()}`);
}

const payloads = ensureCollection("raw", "api_payloads");
ensureIndex(payloads, { run_id: 1, endpoint: 1, page: 1 }, {
  name: "uniq_run_endpoint_page",
  unique: true,
});
ensureIndex(payloads, { payload_sha256: 1 }, { name: "idx_payload_sha256" });
ensureIndex(payloads, { source_system: 1, ingested_at: 1 }, { name: "idx_source_ingested_at" });

ensureCollection("meta", "api_schema_drift");
