/* Evidence maturity vocabulary.

   The backend distinguishes how much a finding can be trusted, and that
   distinction must survive into the UI: a result derived from 2 videos must
   never look as confident as one derived from 20.

   Two canonical backend vocabularies exist and are deliberately NOT merged:

   - `sample_maturity` on channel_performance_baselines and
     feature_performance_observations:
        insufficient → exploratory → directional → actionable
   - `recommendation_strength` on optimization_recommendations:
        exploratory | actionable   (paired with a separate confidence level)

   Each surface renders its own table's vocabulary so the canonical backend
   state is never lost or misrepresented.
*/

export type Maturity =
  | 'insufficient'
  | 'exploratory'
  | 'directional'
  | 'actionable'
  | string

export type MaturityTone = 'neutral' | 'info' | 'warn' | 'healthy'

export interface MaturityMeta {
  label: string
  tone: MaturityTone
  meaning: string
}

export const MATURITY_META: Record<string, MaturityMeta> = {
  insufficient: {
    label: 'Not enough data',
    tone: 'neutral',
    meaning: 'Too few observations to say anything yet.',
  },
  exploratory: {
    label: 'Exploratory',
    tone: 'info',
    meaning: 'An early pattern. Treat as a question to investigate, not a finding.',
  },
  directional: {
    label: 'Directional',
    tone: 'warn',
    meaning: 'A consistent association across enough samples to guide a test.',
  },
  actionable: {
    label: 'Actionable',
    tone: 'healthy',
    meaning: 'Enough evidence to act on directly.',
  },
}

/** Resolve a backend maturity token to its display metadata. */
export function maturityMeta(maturity: string | null | undefined): MaturityMeta {
  const key = (maturity ?? 'insufficient').toLowerCase()
  return (
    MATURITY_META[key] ?? {
      label: maturity ?? 'Unknown',
      tone: 'neutral',
      meaning: 'Unrecognized maturity level.',
    }
  )
}

/** Plain-language meaning of a maturity level, for inline explanation. */
export function maturityMeaning(maturity: string | null | undefined): string {
  return maturityMeta(maturity).meaning
}
