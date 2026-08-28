import { createContext, type ReactNode, useContext } from 'react'

import { useGraphStore, type GraphStore } from '@/stores/graph'

const GraphRuntimeContext = createContext<GraphStore>(useGraphStore)

export function GraphRuntimeProvider({
  store,
  children
}: {
  store: GraphStore
  children: ReactNode
}) {
  return (
    <GraphRuntimeContext.Provider value={store}>
      {children}
    </GraphRuntimeContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useGraphRuntimeStore(): GraphStore {
  return useContext(GraphRuntimeContext)
}
