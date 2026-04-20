import { createBrowserClient } from '@supabase/ssr'

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  )
}

export type Stage = {
  id: number
  race: string
  number: number
  stage_type: string
  date: string | null
  distance_km: number | null
  start_location: string | null
  finish_location: string | null
  profile_score: number | null
  ps_final_25k: number | null
  gradient_final_km: number | null
  vertical_meters: number | null
  sprint_points: unknown[]
  kom_points: unknown[]
  notes: string | null
  image_url: string | null
}

export type Rider = {
  id: string
  user_id: string
  race: string
  holdet_id: string
  name: string | null
  team: string | null
  team_abbr: string | null
  value: number | null
  start_value: number | null
  points: number | null
  status: string
  gc_position: number | null
  jerseys: string[]
  in_my_team: boolean
  is_captain: boolean
}

export type ProbSnapshot = {
  id: string
  user_id: string
  race: string
  stage_number: number
  rider_id: string
  p_win: number | null
  p_top3: number | null
  p_top10: number | null
  p_top15: number | null
  p_dnf: number | null
  source: string
  model_confidence: number | null
  manual_overrides: Record<string, number>
}

export type GameState = {
  id: string
  user_id: string
  race: string
  current_stage: number
  total_stages: number
  my_team: string[]
  captain: string | null
  bank: number
  initial_budget: number
  stages_completed: number[]
  my_rank: number | null
  total_participants: number | null
}

export type BrierRecord = {
  id: string
  stage_number: number
  rider_id: string
  event: string
  model_prob: number | null
  manual_prob: number | null
  actual: number | null
  model_brier: number | null
  manual_brier: number | null
}

export type ValueDelta = {
  id: string
  stage_number: number
  rider_id: string
  delta_json: Record<string, number>
}

export type IntelligenceLog = {
  id: string
  stage_number: number
  stage_summary: string | null
  rider_adjustments: RiderAdjustment[]
  dns_risks: string[]
  stage_notes: string | null
  sources_used: string[]
  created_at: string
}

export type RiderAdjustment = {
  name: string
  p_win_suggested: number
  p_top3_suggested: number
  p_top15_suggested: number
  p_dnf_suggested: number
  reasoning: string
  confidence: 'high' | 'medium' | 'low'
}
