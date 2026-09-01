import { useChannelAccounts, useChannels } from '@/hooks/useChannel'
import type { CPAccount, CPChannel } from '@/api/types'

interface CurrentChannel {
  channel: CPChannel | null
  /** The channel's connected platform account, if any — carries the real
      product identity (e.g. the YouTube channel display name "Orvella"),
      which is more meaningful to the user than the internal CP channel name. */
  primaryAccount: CPAccount | null
  isLoading: boolean
  error: unknown
}

/** Resolves the workspace's current channel.
 *
 * The workspace/channel data model already supports multiple channels per
 * workspace. This hook intentionally picks `channels[0]` — the product is
 * single-channel today (Orvella) and does not yet offer channel switching.
 * Centralizing the "which channel is active" resolution here means that when
 * multi-channel UX is built, only this hook needs to change; no page should
 * independently assume there is exactly one channel or hardcode its identity.
 */
export function useCurrentChannel(workspaceId: string): CurrentChannel {
  const channels = useChannels(workspaceId)
  const channel = channels.data?.[0] ?? null

  const accounts = useChannelAccounts(workspaceId, channel?.id ?? '')
  const primaryAccount = accounts.data?.[0] ?? null

  return {
    channel,
    primaryAccount,
    isLoading: channels.isLoading || (!!channel && accounts.isLoading),
    error: channels.error ?? accounts.error ?? null,
  }
}
