// The one place a score becomes text. null is "Not assessed", never 0 or
// a blank: the audit had no evidence, which is different from scoring badly.
export const scoreText = (score: number | null) => (score === null ? "Not assessed" : String(score));
