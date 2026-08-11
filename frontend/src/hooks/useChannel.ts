import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { YouTubeVerificationResult } from '@/api/types'

export function useChannels(workspaceId: string) {
  return useQuery({
    queryKey: ['channels', workspaceId],
    queryFn: () => api.listChannels(workspaceId),
    enabled: !!workspaceId,
  })
}

export function useChannel(workspaceId: string, channelId: string) {
  return useQuery({
    queryKey: ['channel', workspaceId, channelId],
    queryFn: () => api.getChannelSummary(workspaceId, channelId),
    enabled: !!(workspaceId && channelId),
  })
}

export function useChannelAccounts(workspaceId: string, channelId: string) {
  return useQuery({
    queryKey: ['channel-accounts', workspaceId, channelId],
    queryFn: () => api.listChannelAccounts(workspaceId, channelId),
    enabled: !!(workspaceId && channelId),
  })
}

export function useChannelStrategy(workspaceId: string, channelId: string) {
  return useQuery({
    queryKey: ['channel-strategy', workspaceId, channelId],
    queryFn: () => api.getChannelStrategy(workspaceId, channelId),
    enabled: !!(workspaceId && channelId),
  })
}

export function useCreateChannel(workspaceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; slug: string; description?: string }) =>
      api.createChannel(workspaceId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['channels', workspaceId] })
    },
  })
}

export function useCreatePlatformAccount(workspaceId: string, channelId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { platform_id: string; external_account_id: string; display_name: string }) =>
      api.createPlatformAccount(workspaceId, channelId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['channel-accounts', workspaceId, channelId] })
      void qc.invalidateQueries({ queryKey: ['channel', workspaceId, channelId] })
    },
  })
}

export function useAccountConnectionStatus(
  workspaceId: string,
  channelId: string,
  accountId: string,
) {
  return useQuery({
    queryKey: ['account-connection', workspaceId, channelId, accountId],
    queryFn: () => api.getAccountConnectionStatus(workspaceId, channelId, accountId),
    enabled: !!(workspaceId && channelId && accountId),
    refetchInterval: 30_000,
  })
}

export function useStartYouTubeOAuth(workspaceId: string, channelId: string, accountId: string) {
  return useMutation({
    mutationFn: () => api.startYouTubeOAuth(workspaceId, channelId, accountId),
    onSuccess: (data) => {
      // Redirect the user's browser to Google's authorization URL
      window.location.href = data.authorization_url
    },
  })
}

export function useDisconnectYouTubeAccount(workspaceId: string, channelId: string, accountId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.disconnectYouTubeAccount(workspaceId, channelId, accountId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account-connection', workspaceId, channelId, accountId] })
      void qc.invalidateQueries({ queryKey: ['channel-accounts', workspaceId, channelId] })
    },
  })
}

export function useVerifyYouTubeConnection(workspaceId: string, channelId: string, accountId: string) {
  return useMutation<YouTubeVerificationResult, Error>({
    mutationFn: () => api.verifyYouTubeConnection(workspaceId, channelId, accountId),
  })
}
