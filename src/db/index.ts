import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
    console.warn("DATABASE_URL environment variable is not set. Database connection will fail.");
}

// Disable prefetch as it is not supported for "Transaction" pool mode
const client = postgres(connectionString || "postgres://localhost:5432/dummy", { prepare: false });
export const db = drizzle(client);
