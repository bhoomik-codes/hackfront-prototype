import { useEffect, useState } from 'react'
import { Info, WarningCircle, X } from '@phosphor-icons/react'

import { listeners, pending, publish, type Notification } from './notificationsStore'
export function Notifications({ notice }: { notice: string }) {
  const [items, setItems] = useState<Notification[]>([])
  useEffect(() => {
    const receive = (item: Notification) => setItems(current => {
      const previous = current.find(entry => entry.message === item.message)
      return [...current.filter(entry => entry.message !== item.message).slice(-3), { ...item, error: item.error || !!previous?.error }]
    })
    listeners.add(receive)
    pending.forEach(receive)
    pending.length = 0
    return () => { listeners.delete(receive) }
  }, [])
  useEffect(() => { if (notice) publish(notice) }, [notice])
  return <div className="notification-stack" aria-label="Notifications">{items.map(item => <div key={item.id} className={`notification ${item.error ? 'notification-error' : ''}`} role={item.error ? 'alert' : 'status'} aria-atomic="true">
    {item.error ? <WarningCircle size={22} /> : <Info size={22} />}
    <div><strong>{item.error ? 'Action needs attention' : 'Update'}</strong><p>{item.message}</p></div>
    <button type="button" aria-label="Dismiss notification" title="Dismiss notification" onClick={() => setItems(current => current.filter(entry => entry.id !== item.id))}><X size={18} /></button>
  </div>)}</div>
}
