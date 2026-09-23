'use client'

import { useMemo, useState } from 'react'
import {
  ArrowRight,
  Check,
  ChevronUp,
  CircleHelp,
  Crown,
  Flame,
  LockKeyhole,
  Radio,
  Shield,
  Sparkles,
  TimerReset,
  Trophy,
  Zap,
} from 'lucide-react'

type Phase = 'idle' | 'topic' | 'live' | 'closed'
type Team = { teamName: string; score: number }

const leaderboard: Team[] = [
  { teamName: 'Quantum Crew', score: 12400 },
  { teamName: 'Byte Me', score: 10850 },
  { teamName: 'The Big Brains', score: 9720 },
  { teamName: 'Ctrl Alt Elite', score: 8650 },
  { teamName: 'Null Pointers', score: 7410 },
]

const answers = [
  { key: 'A', label: 'Alan Turing', hint: 'The father of modern computing' },
  { key: 'B', label: 'Claude Shannon', hint: 'Information theory pioneer' },
  { key: 'C', label: 'Grace Hopper', hint: 'Compiler trailblazer' },
  { key: 'D', label: 'John von Neumann', hint: 'Stored-program architect' },
]

function PanelHeading({ eyebrow, title, icon: Icon }: { eyebrow: string; title: string; icon: typeof Trophy }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="font-mono text-[10px] font-bold uppercase tracking-[0.28em] text-cyan-300">{eyebrow}</p>
        <h2 className="mt-2 text-xl font-bold tracking-tight text-white">{title}</h2>
      </div>
      <Icon aria-hidden="true" className="size-5 text-cyan-300" />
    </div>
  )
}

function Leaderboard() {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-4 shadow-2xl shadow-cyan-950/10 sm:p-5">
      <PanelHeading eyebrow="Live standings" title="Leaderboard" icon={Trophy} />
      <div className="mt-5 flex flex-col gap-2">
        {leaderboard.map((team, index) => (
          <div key={team.teamName} className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-black/20 px-3 py-3">
            <span className={`grid size-7 shrink-0 place-items-center rounded-lg font-mono text-xs font-bold ${index === 0 ? 'bg-lime-300 text-black' : 'bg-white/10 text-white/60'}`}>
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-white">{team.teamName}</p>
              <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/10">
                <div className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-fuchsia-400" style={{ width: `${Math.max(27, 100 - index * 15)}%` }} />
              </div>
            </div>
            <span className="font-mono text-xs font-bold text-cyan-200">{team.score.toLocaleString()}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function SideStats({ teamName, score, isController, onClaim }: { teamName: string; score: number; isController: boolean; onClaim: () => void }) {
  return (
    <aside className="flex flex-col gap-4">
      <section className="rounded-2xl border border-fuchsia-400/20 bg-gradient-to-br from-fuchsia-400/10 to-transparent p-5">
        <div className="flex items-center gap-2 text-fuchsia-200"><Shield aria-hidden="true" className="size-4" /><span className="font-mono text-[10px] font-bold uppercase tracking-[0.25em]">Your squad</span></div>
        <p className="mt-5 text-2xl font-black tracking-tight text-white">{teamName}</p>
        <div className="mt-4 flex items-end justify-between border-t border-white/10 pt-4">
          <div><p className="font-mono text-[10px] uppercase tracking-widest text-white/40">Current score</p><p className="mt-1 font-mono text-2xl font-bold text-lime-300">{score.toLocaleString()}</p></div>
          <Flame aria-hidden="true" className="size-6 text-fuchsia-300" />
        </div>
      </section>
      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
        <div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-xl bg-cyan-300/10 text-cyan-300"><Radio aria-hidden="true" className="size-4" /></div><div><p className="text-sm font-bold text-white">Game channel</p><p className="font-mono text-[10px] uppercase tracking-widest text-white/40">Encrypted / active</p></div></div>
        <button onClick={onClaim} className="mt-5 flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border border-cyan-300/40 bg-cyan-300/10 px-4 text-sm font-bold text-cyan-200 transition hover:bg-cyan-300/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-300">
          {isController ? <><Crown aria-hidden="true" className="size-4" /> Controller claimed</> : <><LockKeyhole aria-hidden="true" className="size-4" /> Claim controller</>}
        </button>
      </section>
    </aside>
  )
}

export default function Page() {
  const [phase, setPhase] = useState<Phase>('live')
  const [timer, setTimer] = useState(27)
  const [score, setScore] = useState(6200)
  const [isController, setIsController] = useState(false)
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null)
  const [bountyAvailable, setBountyAvailable] = useState(true)
  const progress = useMemo(() => `${Math.min(100, Math.max(0, (timer / 30) * 100))}%`, [timer])

  return (
    <main className="min-h-screen overflow-hidden bg-[#030712] text-white selection:bg-cyan-300 selection:text-black">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_15%_0%,rgba(34,211,238,0.12),transparent_30%),radial-gradient(circle_at_88%_10%,rgba(217,70,239,0.1),transparent_28%)]" />
      <div className="relative mx-auto min-h-screen max-w-[1500px] px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
        <header className="flex flex-col gap-5 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="flex items-center gap-2"><Sparkles aria-hidden="true" className="size-4 text-lime-300" /><span className="font-mono text-[10px] font-bold uppercase tracking-[0.34em] text-lime-300">Arena // 07</span></div>
            <h1 className="mt-3 text-3xl font-black tracking-[-0.04em] text-white sm:text-5xl">Leader<span className="text-cyan-300">Board</span> Puzzles</h1>
            <p className="mt-2 max-w-xl text-sm text-white/50">A live trivia battleground for teams who think faster than the network.</p>
          </div>
          <div className="flex flex-wrap gap-2" aria-label="Demo phase controls">
            {(['idle', 'topic', 'live', 'closed'] as Phase[]).map((item) => <button key={item} onClick={() => setPhase(item)} className={`min-h-10 rounded-full border px-4 font-mono text-[10px] font-bold uppercase tracking-widest transition ${phase === item ? 'border-cyan-300 bg-cyan-300 text-black' : 'border-white/15 bg-white/[0.04] text-white/50 hover:border-cyan-300/50 hover:text-white'}`}>{item}</button>)}
          </div>
        </header>

        <div className="mt-6 grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)_260px] lg:items-start">
          <div className="order-2 lg:order-1"><Leaderboard /></div>
          <section className="order-1 rounded-2xl border border-cyan-300/20 bg-[#07111f]/90 p-5 shadow-[0_0_70px_rgba(34,211,238,0.08)] sm:p-8 lg:order-2 lg:min-h-[590px]">
            <div className="flex items-center justify-between border-b border-white/10 pb-4"><div className="flex items-center gap-2"><span className="size-2 animate-pulse rounded-full bg-lime-300 shadow-[0_0_12px_#bef264]" /><span className="font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-white/50">{phase === 'live' ? 'Live transmission' : `Event ${phase}`}</span></div><span className="font-mono text-xs text-white/40">Q-004 / 010</span></div>
            <div className="flex min-h-[500px] flex-col justify-center py-8">
              {phase === 'idle' && <div className="text-center"><TimerReset className="mx-auto size-10 text-cyan-300" /><p className="mt-7 animate-pulse font-mono text-2xl font-bold tracking-[0.18em] text-cyan-200 sm:text-4xl">WAITING FOR EVENT...</p><p className="mt-4 text-sm text-white/40">The host is preparing your first challenge.</p></div>}
              {phase === 'topic' && <div><p className="font-mono text-xs uppercase tracking-[0.28em] text-fuchsia-300">Incoming topic</p><h2 className="mt-4 text-4xl font-black tracking-tight sm:text-6xl">The birth of<br /><span className="text-cyan-300">the internet.</span></h2><p className="mt-6 max-w-lg text-white/50">Get ready. The next round will test your knowledge of the people and protocols that connected the world.</p>{bountyAvailable && <button onClick={() => setBountyAvailable(false)} className="mt-10 flex w-full items-center justify-between rounded-2xl border border-fuchsia-300/35 bg-fuchsia-300/10 p-5 text-left transition hover:border-fuchsia-300 hover:bg-fuchsia-300/15 focus-visible:outline-2 focus-visible:outline-fuchsia-300"><span><span className="block font-mono text-[10px] font-bold uppercase tracking-widest text-fuchsia-200">Optional power play</span><span className="mt-2 block text-lg font-bold text-white">Activate Bounty Card</span><span className="mt-1 block text-xs text-white/50">Cost: 2,000 pts · Double your next answer</span></span><Zap aria-hidden="true" className="size-7 text-fuchsia-300" /></button>}</div>}
              {phase === 'live' && <div><div className="flex items-end justify-between"><div><p className="font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-fuchsia-300">Question 04</p><p className="mt-3 text-sm text-white/50">History of computing · worth 1,200 pts</p></div><div className="text-right"><p className="font-mono text-3xl font-black tabular-nums text-lime-300">00:{timer.toString().padStart(2, '0')}</p><p className="font-mono text-[9px] uppercase tracking-widest text-white/40">remaining</p></div></div><div className="mt-5 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-gradient-to-r from-lime-300 via-cyan-300 to-fuchsia-400 transition-all" style={{ width: progress }} /></div><h2 className="mt-9 text-2xl font-bold leading-tight tracking-tight text-white sm:text-4xl">Who is credited with inventing the World Wide Web?</h2><div className="mt-7 grid gap-3 sm:grid-cols-2">{answers.map((answer) => <button key={answer.key} onClick={() => setSelectedAnswer(answer.key)} className={`flex min-h-[76px] items-center gap-4 rounded-xl border p-4 text-left transition focus-visible:outline-2 focus-visible:outline-cyan-300 ${selectedAnswer === answer.key ? 'border-cyan-300 bg-cyan-300/15 shadow-[0_0_25px_rgba(34,211,238,0.16)]' : 'border-white/10 bg-white/[0.03] hover:border-white/30 hover:bg-white/[0.07]'}`}><span className={`grid size-9 shrink-0 place-items-center rounded-lg font-mono text-sm font-black ${selectedAnswer === answer.key ? 'bg-cyan-300 text-black' : 'bg-white/10 text-cyan-200'}`}>{answer.key}</span><span><span className="block font-semibold text-white">{answer.label}</span><span className="mt-1 block text-xs text-white/40">{answer.hint}</span></span></button>)}</div><div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="flex gap-2"><button className="flex min-h-11 items-center gap-2 rounded-xl border border-white/10 px-4 text-xs font-bold text-white/60 hover:border-cyan-300/50 hover:text-cyan-200"><TimerReset aria-hidden="true" className="size-4" /> Freeze</button><button className="flex min-h-11 items-center gap-2 rounded-xl border border-white/10 px-4 text-xs font-bold text-white/60 hover:border-cyan-300/50 hover:text-cyan-200"><CircleHelp aria-hidden="true" className="size-4" /> Peek</button></div><button onClick={() => setScore((value) => value + (selectedAnswer ? 1200 : 0))} className="flex min-h-14 items-center justify-center gap-2 rounded-xl bg-lime-300 px-6 text-sm font-black uppercase tracking-widest text-black shadow-[0_0_28px_rgba(190,242,100,0.2)] transition hover:bg-lime-200 disabled:cursor-not-allowed disabled:opacity-40" disabled={!selectedAnswer}>Submit answer <ArrowRight aria-hidden="true" className="size-4" /></button></div></div>}
              {phase === 'closed' && <div className="text-center"><Trophy className="mx-auto size-14 text-lime-300" /><p className="mt-7 font-mono text-xs font-bold uppercase tracking-[0.3em] text-cyan-300">Session complete</p><h2 className="mt-4 text-4xl font-black tracking-tight sm:text-6xl">THANK YOU<br /><span className="text-fuchsia-300">FOR PLAYING!</span></h2><p className="mt-6 text-white/50">Final score: <strong className="text-lime-300">{score.toLocaleString()} pts</strong></p></div>}
            </div>
          </section>
          <div className="order-3"><SideStats teamName="Neon Nomads" score={score} isController={isController} onClaim={() => setIsController(true)} /><div className="mt-4 flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3"><span className="font-mono text-[10px] uppercase tracking-widest text-white/40">Network signal</span><span className="flex items-center gap-1 text-xs font-bold text-lime-300"><ChevronUp className="size-4" /> 98%</span></div></div>
        </div>
      </div>
    </main>
  )
}
