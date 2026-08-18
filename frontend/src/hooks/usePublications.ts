import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function usePublications(workspaceId: string) {
  return useQuery({
    queryKey: ['publications', workspaceId],
    queryFn: () => api.listPublications(workspaceId),
    enabled: !!workspaceId,
  })
}

export function usePublication(workspaceId: string, publicationId: number | null) {
  return useQuery({
    queryKey: ['publication', workspaceId, publicationId],
    queryFn: () => api.getPublication(workspaceId, publicationId!),
    enabled: !!workspaceId && publicationId !== null,
  })
}

export function usePublicationAnalytics(workspaceId: string, publicationId: number | null) {
  return useQuery({
    queryKey: ['publication-analytics', workspaceId, publicationId],
    queryFn: () => api.getPublicationAnalytics(workspaceId, publicationId!),
    enabled: !!workspaceId && publicationId !== null,
  })
}

export function usePublicationVideoUrl(workspaceId: string, publicationId: number | null) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspaceId || publicationId === null) return

    let objectUrl: string | null = null
    setLoading(true)
    setError(null)

    api.streamPublication(workspaceId, publicationId)
      .then(blob => {
        objectUrl = URL.createObjectURL(blob)
        setBlobUrl(objectUrl)
        setLoading(false)
      })
      .catch(err => {
        setError((err as Error).message ?? 'Failed to load video')
        setLoading(false)
      })

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [workspaceId, publicationId])

  return { blobUrl, loading, error }
}
