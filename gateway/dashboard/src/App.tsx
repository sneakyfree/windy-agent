import { useState, lazy, Suspense } from 'react'
import Layout from './components/Layout'

const Home = lazy(() => import('./pages/Home'))
const Chat = lazy(() => import('./pages/Chat'))
const Personality = lazy(() => import('./pages/Personality'))
const Memory = lazy(() => import('./pages/Memory'))
const Skills = lazy(() => import('./pages/Skills'))
const Identity = lazy(() => import('./pages/Identity'))
const Costs = lazy(() => import('./pages/Costs'))
const Settings = lazy(() => import('./pages/Settings'))

const PAGES: Record<string, React.LazyExoticComponent<() => JSX.Element>> = {
  home: Home,
  chat: Chat,
  personality: Personality,
  memory: Memory,
  skills: Skills,
  identity: Identity,
  costs: Costs,
  settings: Settings,
}

function Loader() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="w-8 h-8 border-2 border-[#00d4ff] border-t-transparent rounded-full animate-spin" />
    </div>
  )
}

export default function App() {
  const [page, setPage] = useState('home')

  const PageComponent = PAGES[page] || Home

  return (
    <Layout page={page} setPage={setPage}>
      <Suspense fallback={<Loader />}>
        <PageComponent />
      </Suspense>
    </Layout>
  )
}
