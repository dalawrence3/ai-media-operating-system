interface Props {
  message?: string
}

export function LoadingState({ message = 'Loading…' }: Props) {
  return (
    <div className="loading-state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{message}</span>
    </div>
  )
}
