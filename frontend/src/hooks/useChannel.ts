import { useQuery } from '@tanstack/react-query'
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
