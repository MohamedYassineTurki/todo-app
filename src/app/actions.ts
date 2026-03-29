"use server";

import { revalidatePath } from "next/cache";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { todos } from "@/db/schema";

export async function getTodos() {
    return await db.select().from(todos).orderBy(todos.createdAt);
}

export async function addTodo(formData: FormData) {
    const text = formData.get("text") as string;
    if (!text || text.trim() === "") return;

    await db.insert(todos).values({ text: text.trim() });
    revalidatePath("/");
}

export async function toggleTodo(id: number, completed: boolean) {
    await db.update(todos).set({ completed: !completed }).where(eq(todos.id, id));
    revalidatePath("/");
}

export async function deleteTodo(id: number) {
    await db.delete(todos).where(eq(todos.id, id));
    revalidatePath("/");
}
