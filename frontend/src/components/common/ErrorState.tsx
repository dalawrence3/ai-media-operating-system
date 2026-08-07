interface Props {
  error: unknown
  retry?: () => void
}

function getMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return 'An unexpected error occurred'
}

export function ErrorState({ error, retry }: Props) {
  return (
    <div className="error-state" role="alert">
      <span aria-hidden="true">⚠</span>
      <span>{getMessage(error)}</span>
      {retry && (
        <button className="btn btn-secondary btn-sm" onClick={retry} style={{ marginLeft: 'auto' }}>
          Retry
        </button>
      )}
    </div>
  )
}
