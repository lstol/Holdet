import { NextRequest, NextResponse } from 'next/server'

export async function POST(req: NextRequest) {
  const apiKey = process.env.ANTHROPIC_API_KEY
  if (!apiKey) {
    return NextResponse.json({ error: 'ANTHROPIC_API_KEY not set' }, { status: 500 })
  }

  const body = await req.json()
  const {
    stage_number, start, finish, stage_type, distance_km,
    profile_score, gradient_final_km, my_team,
  } = body

  const prompt = `You are a cycling analyst for a fantasy cycling game.
Next stage: Stage ${stage_number} — ${start} → ${finish} (${stage_type}, ${distance_km}km).
Profile: ProfileScore=${profile_score ?? 'N/A'}, gradient final km=${gradient_final_km ?? 'N/A'}%.
My current team: ${my_team}.

Search and read these sources for this stage:
- https://sport.tv2.dk — find Emil Axelsgaard's stage ${stage_number} analysis for Giro 2026
- https://inrng.com — find Giro 2026 stage ${stage_number} preview or race coverage
- Search: "giro 2026 stage ${stage_number} ${finish} preview favourites"
- Search: "giro 2026 stage ${stage_number} team tactics startlist"

Return ONLY a JSON object with no preamble, no markdown, no code blocks:
{
  "stage_summary": "2-3 sentence tactical overview in English",
  "rider_adjustments": [
    {
      "name": "rider full name",
      "p_win_suggested": 0.00,
      "p_top3_suggested": 0.00,
      "p_top15_suggested": 0.00,
      "p_dnf_suggested": 0.00,
      "reasoning": "1-2 lines citing source",
      "confidence": "high|medium|low"
    }
  ],
  "dns_risks": ["rider name if mentioned as doubtful or injured"],
  "stage_notes": "anything tactically important not captured per-rider",
  "sources_used": ["url1", "url2"]
}
Only include riders in rider_adjustments if you found specific information about them. Do not invent adjustments.`

  try {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-5',
        max_tokens: 2000,
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages: [{ role: 'user', content: prompt }],
      }),
    })

    if (!res.ok) {
      const err = await res.text()
      return NextResponse.json({ error: err }, { status: res.status })
    }

    const data = await res.json()

    // Extract the final text content block
    const textBlock = data.content?.findLast(
      (b: { type: string }) => b.type === 'text'
    )
    const raw = textBlock?.text ?? ''

    // Strip any accidental markdown code fences
    const clean = raw.replace(/^```json?\s*/i, '').replace(/\s*```$/i, '').trim()

    let parsed
    try {
      parsed = JSON.parse(clean)
    } catch {
      // If we can't parse, return a minimal valid structure with the raw text as summary
      parsed = {
        stage_summary: clean.slice(0, 500) || 'Intelligence fetch returned unparseable response.',
        rider_adjustments: [],
        dns_risks: [],
        stage_notes: '',
        sources_used: [],
      }
    }

    return NextResponse.json(parsed)
  } catch (e: unknown) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : 'Unknown error' },
      { status: 500 }
    )
  }
}
