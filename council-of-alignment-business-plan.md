# Council of Alignment — Lean Business Plan

## One-Line Pitch

"Your AI design is 70% done. The Council takes it to 100% — for 70 cents."

## What It Is

An iterative multi-model design review platform where multiple AI models independently critique your work, a synthesis engine identifies what they agree on and where they disagree, and a tracked changelog shows exactly what improved and who suggested it. Each round compounds. Three rounds typically catch 30+ improvements no single model would find alone.

## Who It's For

**Primary audience (launch):**
- Software architects designing complex systems
- Technical founders building MVPs
- Indie developers shipping serious products

**Secondary audiences (expand later):**
- Researchers strengthening methodology
- Technical writers improving documentation
- Prompt engineers refining systems
- Product managers tightening specs

**Who it's NOT for:**
- Casual ChatGPT users
- People who think "AI is just autocomplete"
- Anyone who doesn't already care about iteration

## The Market

### Competitive Landscape

| Tool | What it does | Monthly price | Gap |
|------|-------------|---------------|-----|
| Council AI | Side-by-side multi-model responses | ~$20/mo | No iteration, no attribution |
| Roundtable | Models debate in real-time | ~$15/mo | No version control, no changelog |
| multiple.chat | Parallel model comparison | ~$20/mo | No synthesis, no tracked evolution |
| **Manual tab-switching** | Copy-paste between ChatGPT/Claude/Gemini | Free (in time) | **This is who we really compete with** |

### The Real Competitor

We're not competing with other tools. We're competing with "I'll just paste it into three tabs." The only way to beat that is to make the Council faster and better than manual — which it is, by hours per project.

### Market Size (realistic)

- AI-assisted developers/architects worldwide: ~5-10 million
- Of those, power users who use multiple models: ~500K-1M
- Of those, who would pay $15/month for a workflow tool: ~1-5%
- Addressable: 5,000-50,000 potential paying users
- Realistic capture in year 1: 200-1,000 users

## Revenue Model

### Pricing

| Tier | Price | Target |
|------|-------|--------|
| Free | $0 | 1 project, 2 rounds, 2 reviewers. Hook them with the Evolution Timeline. |
| Pro BYOK | $15/month ($144/year) | Power users who have their own API keys. This is most users. |
| Pro Managed | $29/month | Convenience users who don't want to manage API keys. |

### Revenue Projections (Conservative)

| Milestone | Users | MRR | ARR | Timeline |
|-----------|-------|-----|-----|----------|
| Launch | 0 | $0 | $0 | Month 0 |
| Early traction | 50 | $750 | $9K | Month 3-6 |
| Establishing | 200 | $3,000 | $36K | Month 6-12 |
| Growing | 500 | $7,500 | $90K | Month 12-18 |
| Mature indie | 1,000 | $15,000 | $180K | Month 18-24 |

These are conservative estimates assuming $15 average revenue per user (mix of BYOK and Managed).

### Revenue Projections (Optimistic)

| Milestone | Users | MRR | ARR |
|-----------|-------|-----|-----|
| Growing | 1,000 | $15,000 | $180K |
| Expanding (new use cases) | 2,500 | $37,500 | $450K |
| Team plans added | 3,000+ | $50,000+ | $600K+ |

## Cost Structure

### Fixed Costs (Monthly)

| Item | Cost | Notes |
|------|------|-------|
| Domain + DNS | ~$1/mo | Already likely owned |
| Hosting (VPS) | $5-20/mo | Hetzner, Railway, or Fly.io |
| OpenRouter account | $0 | Pass-through pricing |
| Stripe | 2.9% + $0.30 per transaction | ~$0.74 per $15 subscription |
| Email (transactional) | $0-5/mo | Resend free tier, then $20/mo |
| **Total fixed** | **~$10-30/month** | |

### Variable Costs (Per Managed User)

| Item | Cost | Notes |
|------|------|-------|
| API costs per active user | ~$3-7/mo | Assuming 5-10 projects/month |
| Managed tier revenue | $29/mo | |
| **Margin per managed user** | **~$22-26/mo** | |

### BYOK Users: Zero Variable Cost

BYOK users pay their own API costs directly to providers. Our only cost is serving the web application. At $15/month per user with ~$0.02/month in server cost per user, the margin is essentially 100%.

### Break-Even

With ~$20/month in fixed costs, break-even is **2 paying users**. This is not a cost-intensive business.

## Startup Costs (One-Time)

| Item | Cost | Notes |
|------|------|-------|
| Maryland LLC formation | ~$100 | Online filing |
| Domain name | ~$12/year | councilofalignment.com or similar |
| Stripe setup | $0 | Free to create |
| Logo/branding | $0-50 | AI-generated or simple text logo |
| Landing page | $0 | Build yourself |
| API credits for testing | ~$10-20 | OpenRouter credits during development |
| **Total startup cost** | **~$125-185** | |

## Legal Structure

### LLC Setup (Maryland)

1. File Articles of Organization with Maryland SDAT (~$100)
2. Get an EIN from IRS (free, online, instant)
3. Open a business bank account (free at most banks)
4. Set up Stripe with the business account
5. Write simple Terms of Service and Privacy Policy (template-based for now)
6. Report income on Schedule C of personal tax return

**When to get a lawyer:** When annual revenue exceeds $50K, or if you get a concerning customer request. Not before.

**When to get an accountant:** When quarterly estimated taxes become relevant (revenue > ~$1K/month consistently). Until then, keep receipts and use basic accounting software.

## Go-to-Market Strategy

### Phase 1: Build in Public (During development)

- Tweet about the build process on X
- Share screenshots of the Evolution Timeline as it takes shape
- Share the MonopolyTrader case study in AI builder communities
- Collect email signups on a simple landing page

### Phase 2: Launch Week

- **Day 1**: Twitter/X thread with the MonopolyTrader story + product screenshots
- **Day 1**: Hacker News "Show HN" post
- **Day 7**: Product Hunt launch
- **Day 14**: Indie Hackers post + dev tool directories

### Phase 3: Content Marketing (Ongoing)

- Weekly "Council Review" content: pick a public design (open-source project, published architecture), run it through the Council, share the v1 → v3 evolution
- This is free marketing that demonstrates the product's value
- Cross-post to X, LinkedIn, dev.to, Hashnode

### Phase 4: Community (Month 3+)

- Discord or community for Council users
- Share reviewer performance insights ("Gemini is best at catching cost issues")
- User-submitted case studies

### The Hook That Sells

The MonopolyTrader story is the launch vehicle:

"I built a trading system. Three AIs reviewed it. They found 30+ structural flaws — hallucinated causality, broken stop-losses, missing backtesting. No single model caught them all. Three rounds of iterative review transformed it from a toy into a research platform. Total API cost: 70 cents."

Then: "So I built a tool that automates the whole process."

One screenshot of the Evolution Timeline does more than any feature list.

## Key Metrics to Track

| Metric | Why It Matters |
|--------|---------------|
| Signups (free) | Top of funnel |
| Free → Paid conversion rate | Product-market fit signal |
| Projects per user per month | Engagement / stickiness |
| Rounds per project (average) | Are people doing iteration or stopping at 1? |
| Changelog accept rate | Is the synthesis engine producing useful suggestions? |
| Churn rate (monthly) | Retention |
| Net Revenue Retention | Are existing users spending more over time? |

**Critical stickiness metric**: If users consistently do only 1 round per project and don't return, the core value prop (iteration) isn't landing. This is the most important thing to watch.

## Kill Criteria

| Signal | Timeframe | Decision |
|--------|-----------|----------|
| CLI prototype synthesis quality is bad | Month 1-2 | Fix synthesis prompts or reconsider approach |
| Web UI launched, <50 paying users after 3 months | Month 6-7 | Is it marketing or product? A/B test messaging. |
| <50 users AND users don't do round 2 | Month 6-7 | Core value not landing. Sunset or major pivot. |
| Steady 50-150 users, growing slowly | Month 6-12 | Keep going. Patience. |
| Revenue stalls below $2K MRR for 6+ months | Month 12+ | Ceiling hit. Consider expanding use cases or sunsetting. |
| Major provider ships native iterative review | Any time | Evaluate if their implementation matches your depth. If yes, pivot. If partial, differentiate harder. |

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Big provider ships native multi-review | Medium | High | Move fast, own the changelog/attribution layer |
| Nobody finds it / marketing fails | Medium | High | Build in public, content marketing, MonopolyTrader case study |
| Users try once and don't return | Medium | Medium | Nail the Evolution Timeline experience, follow up |
| API pricing spikes | Low | Medium | BYOK model insulates us; pass through costs |
| Model quality converges (less value in multi-perspective) | Low | Medium | Pivot to debate/stress-test mode |
| Someone copies the concept | Medium | Low | Execution speed + community + reviewer performance data = moat |

## 12-Month Roadmap

| Month | Focus | Milestone |
|-------|-------|-----------|
| 1-2 | Build Phase 1 (CLI MVP) | Working pipeline: dispatch → review → synthesize → changelog |
| 3 | Build Phase 2 (Web UI) | Workspace + Council View + basic timeline |
| 4 | Launch | HN + X + Product Hunt. First paying users. |
| 5-6 | Iterate | Fix what's broken based on real usage. Improve synthesis. |
| 7-8 | Phase 3 features | Full Evolution Timeline, reviewer stats, debate mode |
| 9-10 | Growth | Content marketing, case studies, community |
| 11-12 | Evaluate + Expand | Hit targets? Expand to writing/research. Miss? Assess why. |

## Summary

The Council of Alignment is a low-cost, high-margin indie SaaS that automates an AI workflow people are already doing manually. The economics are strong (sub-dollar API costs, $15/month pricing, near-zero infrastructure costs), the differentiation is real (no competitor does iterative rounds with tracked attribution), and the proof of concept is documented (MonopolyTrader: 30+ improvements across 3 rounds).

It's not a VC-scale business. It's a focused, profitable indie tool for serious builders. Realistic year-one target: $3K-7K MRR. Realistic ceiling: $15K-50K MRR. Startup cost: under $200.

The biggest risk is not the product — it's whether one person with a full-time job can sustain the marketing effort needed to find and convert the target audience. The mitigation is the MonopolyTrader story, which is a natural, compelling marketing asset that requires no exaggeration.

Build it lean. Launch it loud. Let the Council review itself in public.
