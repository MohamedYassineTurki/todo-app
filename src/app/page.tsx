import { getTodos, addTodo } from "./actions";
import { TodoItem } from "@/components/TodoItem";

export const dynamic = "force-dynamic";

export default async function Home() {
  const todos = await getTodos();

  return (
    <main className="min-h-screen bg-gray-50 flex flex-col items-center py-12 px-4 font-sans text-gray-900">
      <div className="w-full max-w-lg bg-white rounded-xl shadow-lg shadow-gray-200/50 overflow-hidden border border-gray-100 mt-10">
        <div className="p-6 bg-blue-600">
          <h1 className="text-2xl font-bold text-white tracking-tight">Shared Todo List</h1>
          <p className="text-blue-100 text-sm mt-1">Everyone can see and edit this list.</p>
        </div>

        <div className="p-6">
          <form action={addTodo} className="flex gap-2 mb-6">
            <input
              type="text"
              name="text"
              placeholder="What needs to be done?"
              required
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            <button
              type="submit"
              className="px-6 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
            >
              Add
            </button>
          </form>

          {todos.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              <p>No todos yet. Add one to get started!</p>
            </div>
          ) : (
            <ul className="border border-gray-100 rounded-lg overflow-hidden">
              {todos.map((todo) => (
                <TodoItem key={todo.id} todo={todo} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </main>
  );
}
