import type { ModelOptionsResult } from '@hermes/shared'
import { useQuery } from '@tanstack/react-query'
import type * as React from 'react'
import { useMemo } from 'react'

import type { HermesGateway } from '@/hermes'
import { quickModelOptions } from '@/lib/chat-runtime'
import { currentModelCapabilities, findCatalogProvider, modelOptionsQueryKey, requestModelOptions } from '@/lib/model-options'

import { theseusAllowance } from '@/lib/theseus-allowance'

import type { ChatBarState } from './composer/types'

interface ChatBarStateOptions {
  activeGatewayProfile: string
  activeSessionId: null | string
  contextSuggestions: ChatBarState['tools']['suggestions']
  currentModel: string
  currentProvider: string
  gateway: HermesGateway | null
  gatewayOpen: boolean
  modelMenuContent?: React.ReactNode
  modelOptionsOwnerConnectionId?: string
  modelOptionsProfile?: string
  reasoningMenuContent?: React.ReactNode
  requestModelOptionsForOwner?: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

// The composer's model / tools / voice state, backed by the model-options query
// for the active session's profile.
export function useChatBarState({
  activeGatewayProfile,
  activeSessionId,
  contextSuggestions,
  currentModel,
  currentProvider,
  gateway,
  gatewayOpen,
  modelMenuContent,
  modelOptionsOwnerConnectionId,
  modelOptionsProfile,
  reasoningMenuContent,
  requestModelOptionsForOwner
}: ChatBarStateOptions): ChatBarState {
  const modelOptionsQuery = useQuery<ModelOptionsResult>({
    queryKey: modelOptionsQueryKey(
      modelOptionsProfile || activeGatewayProfile,
      activeSessionId,
      modelOptionsOwnerConnectionId
    ),
    queryFn: () =>
      requestModelOptions({
        gateway: gateway || undefined,
        profile: modelOptionsProfile || activeGatewayProfile,
        request: requestModelOptionsForOwner,
        sessionId: activeSessionId
      }),
    refetchInterval: import.meta.env.VITE_THESEUS_DESKTOP === '1' ? 60_000 : false,
    enabled: gatewayOpen
  })

  const catalog = modelOptionsQuery.data
  const row = findCatalogProvider(catalog?.providers ?? [], currentProvider || catalog?.provider || '')
  const allowance = import.meta.env.VITE_THESEUS_DESKTOP === '1' && gatewayOpen
    ? theseusAllowance(row, currentModel || catalog?.model || '')
    : undefined

  const quickModels = useMemo(
    () => quickModelOptions(modelOptionsQuery.data, currentProvider, currentModel),
    [currentModel, currentProvider, modelOptionsQuery.data]
  )

  const caps = currentModelCapabilities(modelOptionsQuery.data, currentProvider, currentModel)
  const supportsReasoning = caps?.reasoning
  const supportsFast = caps ? Boolean(caps.fast) : undefined
  const supportsUltrafast = caps ? Boolean(caps.ultrafast) : undefined

  return useMemo<ChatBarState>(
    () => ({
      allowance,
      model: {
        model: currentModel,
        provider: currentProvider,
        canSwitch: gatewayOpen,
        loading: !gatewayOpen || (!currentModel && !currentProvider),
        modelMenuContent,
        quickModels,
        reasoningMenuContent,
        supportsFast,
        supportsReasoning,
        supportsUltrafast
      },
      tools: {
        enabled: true,
        label: 'Add context',
        suggestions: contextSuggestions
      },
      voice: {
        enabled: true,
        active: false
      }
    }),
    [
      allowance,
      contextSuggestions,
      currentModel,
      currentProvider,
      gatewayOpen,
      modelMenuContent,
      quickModels,
      reasoningMenuContent,
      supportsFast,
      supportsReasoning,
      supportsUltrafast
    ]
  )
}
