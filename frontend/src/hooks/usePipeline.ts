import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { StageArtifact, DiagnosticReport } from '@/api/types'

export function usePipelines(workspaceId: string, status?: string, channelId?: string) {
  return useQuery({
    queryKey: ['pipelines', workspaceId, status, channelId],
    queryFn: () => api.listPipelines(workspaceId, { status, channelId }),
    enabled: !!workspaceId,
    refetchInterval: 15_000,
  })
}

export function usePipeline(workspaceId: string, pipelineId: string) {
  return useQuery({
    queryKey: ['pipeline', workspaceId, pipelineId],
    queryFn: () => api.getPipeline(workspaceId, pipelineId),
    enabled: !!(workspaceId && pipelineId),
    refetchInterval: 10_000,
  })
}

export function usePipelineMutations(workspaceId: string) {
  const qc = useQueryClient()
  const invalidate = (pipelineId?: string) => {
    void qc.invalidateQueries({ queryKey: ['pipelines', workspaceId] })
    if (pipelineId) {
      void qc.invalidateQueries({ queryKey: ['pipeline', workspaceId, pipelineId] })
    }
  }

  const pause = useMutation({
    mutationFn: (id: string) => api.pausePipeline(workspaceId, id),
    onSuccess: (_data, id) => invalidate(id),
  })

  const resume = useMutation({
    mutationFn: (id: string) => api.resumePipeline(workspaceId, id),
    onSuccess: (_data, id) => invalidate(id),
  })

  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelPipeline(workspaceId, id),
    onSuccess: (_data, id) => invalidate(id),
  })

  const recover = useMutation({
    mutationFn: (id: string) => api.recoverPipeline(workspaceId, id),
    onSuccess: (_data, id) => invalidate(id),
  })

  const executeStage = useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: string }) =>
      api.executePipelineStage(workspaceId, id, stage),
    onSuccess: (_data, { id }) => invalidate(id),
  })

  const advanceStage = useMutation({
    mutationFn: ({ id, stage, artifact_id, artifact_type }: {
      id: string; stage: string; artifact_id?: string; artifact_type?: string
    }) => api.advancePipelineStage(workspaceId, id, { stage, artifact_id, artifact_type }),
    onSuccess: (_data, { id }) => invalidate(id),
  })

  return { pause, resume, cancel, recover, executeStage, advanceStage }
}

export function useStageArtifact(
  workspaceId: string,
  pipelineId: string,
  stage: string,
  enabled = true,
) {
  return useQuery<StageArtifact>({
    queryKey: ['stage-artifact', workspaceId, pipelineId, stage],
    queryFn: () => api.getStageArtifact(workspaceId, pipelineId, stage),
    enabled: !!(workspaceId && pipelineId && stage && enabled),
  })
}

export function usePipelineDiagnostics(
  workspaceId: string,
  pipelineId: string,
  stage: string,
  enabled = true,
) {
  return useQuery<DiagnosticReport>({
    queryKey: ['pipeline-diagnostics', workspaceId, pipelineId, stage],
    queryFn: () => api.getPipelineDiagnostics(workspaceId, pipelineId, stage),
    enabled: !!(workspaceId && pipelineId && stage && enabled),
  })
}

export function useStartPipeline(workspaceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      channel_id: string
      idempotency_key: string
      platform_account_id?: string
      end_stage?: string
      topic_id?: number | null
    }) => api.startPipeline(workspaceId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['pipelines', workspaceId] })
    },
  })
}
