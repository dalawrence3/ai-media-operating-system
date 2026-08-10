import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

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
