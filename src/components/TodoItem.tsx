"use client";

import { useTransition } from "react";
import { toggleTodo, deleteTodo } from "@/app/actions";
import { Check, Trash2 } from "lucide-react";

type Todo = {
    id: number;
    text: string;
    completed: boolean;
};

export function TodoItem({ todo }: { todo: Todo }) {
    const [isPending, startTransition] = useTransition();

    return (
        <li className="flex items-center justify-between p-3 border-b border-gray-100 last:border-0 hover:bg-gray-50 transition-colors">
            <div className="flex items-center gap-3 flex-1">
                <button
                    onClick={() => startTransition(() => toggleTodo(todo.id, todo.completed))}
                    disabled={isPending}
                    className={`shrink-0 w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors ${todo.completed ? "bg-green-500 border-green-500 text-white" : "border-gray-300"
                        }`}
                >
                    {todo.completed && <Check size={14} strokeWidth={3} />}
                </button>
                <span className={`text-gray-800 ${todo.completed ? "line-through text-gray-400" : ""}`}>
                    {todo.text}
                </span>
            </div>
            <button
                onClick={() => startTransition(() => deleteTodo(todo.id))}
                disabled={isPending}
                className="text-gray-400 hover:text-red-500 transition-colors p-2 rounded-md hover:bg-red-50"
            >
                <Trash2 size={18} />
            </button>
        </li>
    );
}
