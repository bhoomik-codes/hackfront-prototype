export type Notification = { id: number; message: string; error: boolean }
let sequence = 0
export const listeners = new Set<(item: Notification) => void>()
export const pending: Notification[] = []
export function publish(message: string, error = false) {
  const item = { id: ++sequence, message, error }
  if (!listeners.size) pending.splice(0, pending.length, ...pending.slice(-3), item)
  for (const listener of listeners) listener(item)
}
export function reportError(error: unknown, fallback: string): string {
  const message = error instanceof Error && error.message ? error.message : fallback
  publish(message, true)
  return message
}
