"""NCC brand pack definitions used as the portfolio's structured operating layer."""

from __future__ import annotations

NCC_BRAND_DEFINITIONS = (
    {
        "name": "Gorilla X Labs",
        "slug": "sixer-supreme",
        "status": "active",
        "description": "Account management and expansion account with reporting, range reviews and sub-distributor management.",
        "context_md": """# Gorilla X Labs

Status: Active client.

The GXL bot must manage the GXL-specific account management, expansion, retail/distribution activity, product/range information, pricing/margins, sub-distributors, monthly reporting, range reviews, open actions, follow-ups, invoices/payment issues, contract/scope, risks and decisions required.

- Account management and expansion account.
- Monthly reporting and range reviews.
- Sub-distributor management and commercial follow-up.
- Track pricing, margins, invoices, payment issues and contracts.
- Flag decisions, risks and open actions for Mitch.
- Do not invent missing data; mark gaps as [VERIFY] rather than guessing.
""",
    },
    {
        "name": "Mr Whippy",
        "slug": "la-pret",
        "status": "active",
        "description": "Dib Group / Mr Whippy fuel-site accounts and sales development across convenience and grocery channels.",
        "context_md": """# Mr Whippy

Status: Active client.

Treat Dib Group and Mr Whippy as the same company for the relevant NCC/Xero context.

- Understand the Mr Whippy / Dib Group relationship and relevant fuel-site accounts.
- Track BP Jasbe, Ampol, Liberty, Grocery, Drakes, Supabarn and related retail opportunities.
- Manage soft-serve business development, cups/products, venue offers and grocery push.
- Monitor national pub, club, hotel, restaurant and childcare opportunities.
- Track retailer targets, reviews, reporting, invoicing, open actions, risks and decisions.
- Flag overdue follow-ups and use current verified information over old brand pack statements.
""",
    },
    {
        "name": "Enhancd Performance",
        "slug": "meraki",
        "status": "active",
        "description": "Retail and distribution strategy across pharmacy, petrol/convenience, mining and international growth opportunities.",
        "context_md": """# Enhancd Performance

Status: Active client.

Follow the Enhancd-specific information in the Brand Pack, including product/range knowledge, retail/distribution strategy, pharmacy, contract catering, petrol/convenience, mining, international opportunities, NZ distribution, Olympia opportunities, retailer/distributor outreach, gummy accounts, DCO, account follow-ups, samples, invoices, 90-day strategy, channel strategy, open actions, decisions and data that must be verified.

- Track product range, retail and distribution strategy, and channel-specific progress.
- Monitor pharmacy, contract catering, petrol/convenience and mining opportunities.
- Treat strategy drafts as unconfirmed unless the document says they were adopted.
- Verify data and decision points before using them as operational fact.
""",
    },
    {
        "name": "After Sweat Co",
        "slug": "marnia",
        "status": "active",
        "description": "Gym distribution objective with D2C, Shopify and wholesale commercial planning.",
        "context_md": """# After Sweat Co

Status: Active client.

The specific objective is to lock gym distribution.

- Track biodegradable post-workout wipes, gym distribution objective and D2C/Shopify operations.
- Maintain current partner detail for gyms and studios, including Wipes on Tap.
- Monitor product range, RRP, COGS, margin and wholesale pricing where confirmed.
- Prioritise gym/studio distribution, health/recovery positioning and channel strategy.
- Track reports, forms, risks, follow-ups and decisions.
""",
    },
    {
        "name": "Onest Health",
        "slug": "kaizen-gym",
        "status": "onboarding",
        "description": "New client onboarding with channel strategy, Xero setup and commercial checks.",
        "context_md": """# Onest Health

Status: New client / onboarding.

Handle onboarding, internal setup, Xero setup, products, channels, samples, Olympia activity, invoicing, retail/channel strategy, DCO, Compass, Metro Petroleum, distribution, open actions, follow-ups, decisions and data verification.

- Use the NCC onboarding flow: Questionnaire → 90-day roadmap → 3-page channel playbook → Xero setup → focus blocks.
- Do not claim onboarding is complete unless the stages are actually completed.
- Verify data before using it in plans or commercial discussions.
""",
    },
    {
        "name": "Vitalus",
        "slug": "harry-s-retail",
        "status": "onboarding",
        "description": "New client onboarding with a thin data set requiring data-gap capture before roadmap work.",
        "context_md": """# Vitalus

Status: New client / onboarding / thin data.

The bot must first identify and fill data gaps before proceeding. It must not invent missing Vitalus information.

- Build out owner, contacts, product range, COGS, wholesale pricing, RRP, current accounts and target channels.
- Confirm agreement, agreement term, invoice cadence and relationship to Onest if relevant.
- Once data is known, run the NCC onboarding flow: Questionnaire → 90-day roadmap → 3-page channel playbook.
- Use [VERIFY] or [INFERRED] flags appropriately when information is still missing.
""",
    },
    {
        "name": "World Wide Lashes",
        "slug": "rashid-textile",
        "status": "active",
        "description": "Thin-data active client requiring proactive data gathering across Gmail and Drive.",
        "context_md": """# World Wide Lashes

Status: Active client / thin data.

Because the brand has thin data, the bot must actively identify missing information from Gmail and Drive rather than inventing it.

- Track brand strategy, content, growth, products, channels, current content/strategy deliverables and KPIs.
- Confirm agreement terms, invoices, duplicate invoice checking, open actions, follow-ups, risks and decisions.
- Use current Gmail and Drive evidence to fill gaps before making factual claims.
- Flag missing or conflicting information for verification.
""",
    },
    {
        "name": "Crayv'n Snacks",
        "slug": "the-faisalabad-links",
        "status": "active",
        "description": "Starts 3 October 2026 with onboarding and commercial strategy milestones.",
        "context_md": """# Crayv'n Snacks

Status: Starts 3 October 2026.

Follow the Crayv'n Brand Pack exactly, including onboarding, agreement, start date, product/range information, commercial strategy, Month 1, Month 2, Month 3, performance targets, retail/channel strategy, open actions, ABN/ACN verification, invoicing, follow-ups, decisions and risks.

- Track onboarding, agreement milestones and start-date confirmation.
- Where dates conflict or information is missing, mark it for verification rather than assuming it is correct.
- Keep a clear record of open actions, supplier and client actions, and commercial decisions.
""",
    },
    {
        "name": "Slimmies",
        "slug": "araish",
        "status": "parked",
        "description": "Parked/overdue invoice brand requiring clear confirmation before restarting.",
        "context_md": """# Slimmies

Status: Parked / overdue invoice.

Understand the current parked status, invoice/payment situation, existing brand context, outstanding items and what would need to happen before work resumes.

- Do not treat this as an active workstream unless current information confirms it has become active again.
- Review invoice and payment blockers, outstanding issues and prior context before re-engagement.
- Treat work resumption as contingent on confirmation from the client or internal approval.
""",
    },
    {
        "name": "R3sup",
        "slug": "zainab-humayoun",
        "status": "parked",
        "description": "Parked and unpaid with no active workstream unless confirmed otherwise.",
        "context_md": """# R3sup

Status: Parked / unpaid.

Understand the parked status, payment situation, existing context, outstanding issues and whether work is currently active.

- Do not assume active work.
- Review payment blockers and outstanding issues before recommitting to tasks.
- Track any movement only when current information confirms it is active again.
""",
    },
    {
        "name": "Prospects and Unclear Relationships",
        "slug": "zen",
        "status": "prospect",
        "description": "Grouped prospect and unclear relationships for J'yug, Fuelys, Faction Labs, Simply Wellness / Hangry Boy and other unclear relationships.",
        "context_md": """# Prospects and Unclear Relationships

Status: Grouped prospect and unclear relationship context.

Use one grouped prospect/unclear context for J'yug, Fuelys, Faction Labs, Simply Wellness / Hangry Boy and other unclear or prospect relationships listed in the document.

- Keep them grouped because there is insufficient data for separate standalone bots.
- If a prospect becomes active, it can later be split into its own Brand Pack and bot.
- Track prospect status, outreach, contact details, channel opportunities and any evidence of commercial relevance.
""",
    },
    {
        "name": "NCC Outreach and New Brand Prospecting",
        "slug": "ww-fabrics",
        "status": "active",
        "description": "New brand prospecting, CRM and outreach tracking across inbound and outbound opportunities.",
        "context_md": """# NCC Outreach and New Brand Prospecting

Status: Active portfolio function.

Handle new brand prospecting, CRM, prospect status, outreach, cold emails, Instagram DMs, trade-show outreach, distributor/buyer contacts, follow-ups, existing prospect checking, outreach documents and brand prospect research.

- Follow the confirmed outreach rules exactly.
- Use only current approved outreach rules and templates.
- Do not rely on old or superseded outreach templates if the Brand Pack marks them as superseded.
- Track brand prospect research, channels, contact opportunities and follow-up dates.
""",
    },
    {
        "name": "Applied Nutrition",
        "slug": "the-social-links",
        "status": "prospect",
        "description": "Prospect account with product, retail strategy and international opportunities requiring verification.",
        "context_md": """# Applied Nutrition

Status: Prospect.

The bot must follow the Applied Nutrition Brand Pack exactly and handle prospect/account context, product information, retail strategy, channel strategy, distribution, international opportunities, Olympia activity, onboarding, open actions, commercial opportunities, decisions, follow-ups, risks and information requiring verification.

- Do not treat it as an active client until current information confirms onboarding.
- Track product range, retail and channel opportunities, and any open commercial interest.
- Flag any unverified or conflicting information for Mitch before it is used operationally.
""",
    },
    {
        "name": "VYB Energy",
        "slug": "fantasia",
        "status": "active",
        "description": "Mitch-owned brand with product development, regulatory and supply-side issues.",
        "context_md": """# VYB Energy

Status: Mitch-owned brand.

Handle product/NPD context, regulatory issue, TGA situation, Australian pathway, suppliers, regulatory contacts, freight issue, manufacturing, export opportunities, legal/regulatory matters, open decisions and product/commercial development.

- Keep the multiple supplier-side contacts distinct and do not merge them together.
- Track regulatory and freight issues, manufacturing and export opportunities.
- Surface open decisions and risk items for Mitch.
""",
    },
    {
        "name": "VYVA Energy Jelly Sticks",
        "slug": "shehnoor-jewels",
        "status": "active",
        "description": "Mitch-owned NPD brand tracking development, packaging and commercial planning.",
        "context_md": """# VYVA Energy Jelly Sticks

Status: Mitch-owned brand / NPD.

Follow the VYVA-specific Brand Pack and handle NPD, packaging, product development, samples, manufacturing, commercial planning, retail/channel strategy, open actions, decisions and development status.

- Track development status and sample requests.
- Monitor packaging, manufacturing and retail channel planning.
- Keep decisions and commercial actions visible for Mitch.
""",
    },
    {
        "name": "sofi CUP",
        "slug": "charmuse",
        "status": "active",
        "description": "Mitch-owned NPD brand with packaging, samples and channel opportunities.",
        "context_md": """# sofi CUP

Status: Mitch-owned brand / NPD.

Follow the sofi CUP Brand Pack and handle NPD, product development, samples, packaging, commercial planning, retail/channel opportunities, open actions, decisions and development status.

- Track sample development, packaging and channel planning.
- Keep open actions and development decisions visible to the Chief of Staff.
""",
    },
)


NCC_BRAND_SNAPSHOTS = {
    "sixer-supreme": """# Gorilla X Labs | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 6. Owner: Lee. Fee $3,000 + GST monthly ($3,300); invoice around the 4th to 5th. Agreement began 12 January 2026, 12-month term, 30-day exit either side. Trial ended 11 May.
- Sports nutrition range: RTD cans, RTD protein, powders, gummies, cookies and bars. RTD protein working structure: OTR $3.55, RRP $7.99; final position needs verification.
- Reported account opportunities include BP Jasbe / Metro Petroleum, Ampol, 7-Eleven, Viva, APCO, New Sunrise Group, Ezymart, Compass / TDg, Go Vita and international distributors. Historical account statuses need checking.
- Separate Jasbe promotion actions: confirm 2 for $8 promotion, allocation to about 40 Metro Petroleum sites, dates, stock and sell-through.
- New Sunrise letter of offer, Ampol allocation, 7-Eleven review and Viva pricing were waiting items. JB Metro barcodes and new line form were high priority; leave pricing blank for Mitch.
- GXL invoices have been late; chase promptly from due date and flag work outside scope. Do not treat GXL and Nexus as the same business.
- Olympia outcomes and artwork status are unknown. Confirm before reporting or making commitments.""",
    "la-pret": """# Mr Whippy | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 6/7. Dib Group and Mr Whippy are the same company; Xero billing name is Dib Group. Monthly fee reported as $3,700; verify the September invoice and cadence in Xero.
- Ice cream and soft serve. Fuel accounts include Ampol, BP and Liberty; BP Jasbe sites include Surry Hills, Tower, North Willoughby, Lane Cove, Oakes and Normanhurst.
- Supabarn freezer performance was reported as positive. Drakes current review is signed off; next review January 2027. A separate two-freezer trial was proposed, not confirmed.
- Venue offer is named Spring into Summer Special and runs to 30 November 2026. Serve price conflicts between $4 and $5; confirm before using. Never call it Expo Special or use 31 November.
- Open actions to verify: airline groups, Woolworths/Coles, child play centres, Drakes freezer trial, APCO, Perth enquiry and venue prospecting.
- Product cup count/flavours and package economics remain uncertain. Olympia outcomes and campaign responses need checking.""",
    "meraki": """# Enhancd Performance | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 7. Position as everyday performance across sleep, focus and breathing, not only sleep gummies. HASTA and product claims are compliance-sensitive; use current approved evidence.
- Monthly fee $3,600 inc GST, invoiced around the 22nd to 25th. Older $3,630 invoices are superseded; Olympia add-on INV-0046 for $660 was overdue at the last check.
- Products/prices in notes: sleep gummies $39.99 wholesale / $79.99 RRP; focus gummies same pricing with launch status uncertain; Air Flow nasal strips $15.99 / $34.99. AU COGS not recorded.
- NZ distribution negotiations with Nutrition Systems NZ and Raedon NZ are separate live opportunities. Indicative offer $25.90 ex GST per pack, MOQ 1,000; track terms, SKUs, freight, compliance and launch separately.
- Onboarding strategy: gyms/specialists and health food first, pharmacy from Month 3; 15 to 25 target accounts in 90 days. Treat targets as plans, not achieved results.
- Verify Focus launch, signed account list, both NZ opportunities, DCO/Paul PHD follow-ups and Olympia outcomes.""",
    "marnia": """# After Sweat Co | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 7. Founder: Gabbie, a physiotherapist. Objective: secure gym distribution. Monthly fee $2,750 inc GST; Olympia add-on INV-0047 for $440 was inferred, not confirmed.
- Biodegradable post-workout wipes. D2C via Shopify/Stripe was reported at about $500 to $600 monthly, with five gym/studio partners and a Wipes on Tap subscription.
- Face wipes RRP $13.95, COGS about $6.00; body wipes RRP $15.95, COGS about $7.40. Recovery cream timing is unknown. Wholesale prices in an old roadmap were AI-drafted, not confirmed.
- Priority: gym and Pilates studios, then indie supplement stores, then pharmacy after compliance prep. Roadmap quantities, venue names and tactics are not verified commitments.
- Open actions to verify: QE Food Stores, Wholelife, Olympia outcomes/stock reconciliation, current gym accounts and next invoice.""",
    "kaizen-gym": """# Onest Health | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 6/7. New client onboarding. Monthly fee $3,300; INV-0042 was overdue at the last check. Olympia fee conflicts: $600 in notes, about $700 in proposal, $770 invoiced (inferred).
- Owners Lachlan and Ryan. Sensitive: Mitch is a former employee and National Sales Director. Never mention his Onest employment in communications to Onest.
- Product range in notes: Hyperburn, Protein Water, Hyperload, Hyperpump and Whey Protein.
- September agreement redlines removed in-term exclusivity and restraint, moved invoicing in advance with 14-day terms, and protected other-client confidentiality. Signature status is uncertain.
- Proposed channels/accounts include DCO, Terry White Chemmart, Chemmaxx, Compass, BP, Metro Petroleum, Sodexo and boutique distributors. Treat site counts and targets as proposal assumptions until verified.
- Verify agreement signature, Xero setup/retainer amount, samples to Compass and DCO, Metro go-live and Olympia outcomes.""",
    "harry-s-retail": """# Vitalus | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 7/8, second only to Applied Nutrition in the supplied priority list. New client onboarding; data is thin.
- Monthly fee reported as $3,500, but GST basis is uncertain. INV-0043 was raised for September.
- Products, contacts, channels, agreement terms, invoice cadence and relationship to Onest are not recorded in the source pack.
- Verify Xero setup and whether Vitalus was represented at Olympia before assuming samples or add-on charges.
- Build owner/contact, SKU, COGS, wholesale/RRP, accounts, target channels and signed terms from current Gmail/Drive evidence or Mitch. Do not invent gaps.""",
    "rashid-textile": """# World Wide Lashes | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 6/7. Beauty/lashes. Monthly fee $2,500 inc GST; invoice cadence around the 16th to 19th.
- INV-0051 and INV-0052, each $2,500 on the same day, may be duplicates. Check Xero before chasing or voiding either.
- Owner/contacts, products, channels, deliverables, KPIs and agreement terms are not captured in the pack. Gather from current records.
- Confirm portfolio priority with Mitch, duplicate invoice resolution and next invoice timing.""",
    "the-faisalabad-links": """# Crayv'n Snacks | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive, Xero and signed agreements were not checked. Newer records win; verify conflicts with Mitch.

- Grade 6/7. Contacts: Julian Diprose and Sam. Commencement 3 October 2026; 3-month fixed term. Agreement Schedule still needed the 3 October date and ABN/ACN before signature.
- Month 1: $3,500 inc GST upfront. Months 2 and 3: $2,000 base each, with capped additive performance components up to $2,000 and $2,500 respectively. Targets were to be agreed or deferred until Month 1 data.
- Scope: retail distribution across BP/P&C, Metro, IGA via CSA/sub-distributors and gym networks. Fee schedule controls; “Tier 1” is only the agreement package label.
- Verify signature, ABN/ACN, Month 1 invoice/payment and target approach. Start outreach only within approved scope; do not assume SKU, stock or margin facts.""",
    "araish": """# Slimmies | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive and Xero were not checked. Newer records win; verify conflicts with Mitch.

- Parked because it had not paid. Entity noted as KLN Management. Grade 6.
- Monthly fee $3,000 + GST ($3,300). INV-0041 for $3,300 was the oldest overdue invoice in the 21 September notes.
- Do no new work until paid. Draft a firm owner-to-owner chase for Mitch to approve and send.
- Verify whether payment arrived and whether Mitch wants to continue, formally pause or exit. Do not restart work without confirmation.""",
    "zainab-humayoun": """# R3sup | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive and Xero were not checked. Newer records win; verify conflicts with Mitch.

- Parked, unpaid and no active workstream as of mid-September. Grade 5.
- Fee was listed as $3,300 “maybe”; do not treat as agreed.
- Product, contacts, accounts and agreement terms are not recorded. Check Xero for any invoice/payment.
- No proactive work until Mitch reactivates. Ask whether to chase, pause formally or exit.""",
    "zen": """# Prospects and Unclear Relationships | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Gmail, Drive and Xero were not checked. Verify current status before contact.

- Keep records grouped until signed/active. J'yug: contact Rashmee; NCC agreement sent, awaiting signature; perfume is new channel territory.
- Fuelys: protein gummies brand, not a retail channel; discovery/proposal/agreement/onboarding next if still live.
- Faction Labs: Warren Clampit; Olympia international-distribution follow-up. Client/prospect/partner status unclear; invoice only if Mitch confirms.
- Simply Wellness / Hangry Boy: status, scope, products, fee and open work unverified. HaraLine appeared once as a discovery-call name; ask before acting.
- Dib Group is Mr Whippy and belongs in that business record, not here.""",
    "ww-fabrics": """# NCC Outreach and Prospecting | Pack snapshot, 30 September 2026

Snapshot from historical chat notes only. Check the live Google Doc CRM and brand_outreach_crm.xlsx before contacting anyone.

- Goal: 1 to 2 new retainer clients; master sheet target 1,000 contacts. Current prospecting spans Australian consumer brands and categories; international brands entering Australia are secondary.
- Fit score 1 to 5. Retainer clients only; no commission-only deals. Label guessed email patterns as unverified.
- Use the confirmed short cold email from the source pack; do not recap a brand's own information or use the superseded long template.
- Check prior outreach before re-contacting: Eat Cheeky, Wild Sandalwood, Eat Life Lab, Rip'N'Ramen, Keeper Health, Total XP, Adventcha, Conq Recovery, CMC Gold, Juicy Bite, Datefix, Wellthy, Dissolvd, Honey Dept and Goodvice.
- Apollo.io is used for contact research. Trade show emails request a meeting but never book Mitch's calendar. Draft only; approval required before sending.""",
    "the-social-links": """# Applied Nutrition | Pack snapshot, 30 September 2026

Research from public company materials and Australian retail evidence, not verified with Applied Nutrition. UK product specs are not Australian specs. Onboarding status, scope, fee and contract are unverified.

- Grade 9/10 and highest-priority prospect. Promote relevant emails, meetings and follow-ups to the top of the daily brief.
- Keep Applied Nutrition core, ABE, BodyFuel and Endurance as separate ranges. “Mellow” bars were mentioned by Mitch but not verified; do not merge with MOMENT.
- Working channel hypothesis only: ABE cans/bars in P&C; selected powders/snacks in grocery; selected creatine/protein/wellness in pharmacy; specialty for broader range. Not agreed strategy.
- Verify AU formulation, labels, claims, prices, case packs, compliance, current distributor/account ownership, stock, sell-through and existing listings before proposing actions.
- First onboarding map: AU business/distributors/accounts, AU SKU and compliance list, commercial terms, retailer pipeline, stock/forecast, marketing assets and 90-day plan. Never assume UK specs are legal or sold in Australia.""",
    "fantasia": """# VYB Energy | Pack snapshot, 30 September 2026

Mitch-owned brand, not an NCC consulting client. Snapshot from historical notes; regulatory and shipment status must be verified with current documents/advisers.

- Nicotine-free caffeine pouches; four flavours. Confirmed 40 mg caffeine per pouch. 50 mg is superseded. Do not call caffeine natural; source is caffeine anhydrous. RRP $21 inc GST; COGS $3.10. Recalculate margins at current RRP before quoting.
- Shipment PFG9731 was rejected for import. TGA compliance notice RC-078895 response due 18 September; lodgement/reply status is unknown. Do not give legal/regulatory conclusions as fact.
- AUST L route and export of rejected stock were being considered. Shan is regulatory adviser. Obtain current written advice before import, claims or launch.
- Sally (freight dispute), Steven (manufacturer) and Stella (broker) are separate contacts. Do not conflate. Pay freight only on formal invoice with independent bank verification.
- Verify current TGA correspondence, export buyer/price unit, AUST L evidence and Sally dispute outcome. VYB consulting time is parked.""",
    "shehnoor-jewels": """# VYVA Energy Jelly Sticks | Pack snapshot, 30 September 2026

Mitch-owned NPD. Snapshot from historical notes; regulatory classification is unverified.

- Energy jelly stick concept, 40 mg caffeine per stick, six flavours: Cherry Nights, Berry Afterhours, Citrus Glow, Blue Razz, Mango Heat and Watermelon Rush.
- Same caffeine regulatory issue as VYB may apply. Before Australian import/ranging, obtain current advice from Shan/Stephens Lawyers. Export-first was only a possibility, not a decision.
- Manufacturer, MOQ, COGS, pricing, pack format and launch date were not recorded. Pull packaging and supplier quotes from Drive.""",
    "charmuse": """# sofi CUP | Pack snapshot, 30 September 2026

Mitch-owned NPD. Snapshot from historical notes; sample has not been assessed and production was not ordered.

- Portable two-compartment cereal and milk cup. Name “sofi CUP” and slogan “NO SOGGY CEREAL. EVER.” are locked; do not reopen.
- Visual direction: purple hero colour, transparent cup, navy/cream secondary colours. Sample shipment tracking was recorded in the pack; verify delivery before action.
- Supplier quote about US$1.90 per unit at 3,000 MOQ. Target RRP about A$29.95. Landed cost and channel margin model still needed.
- Assess samples for quality, leaks, milk compartment and dishwasher safety. Then confirm landed costs, packaging/barcodes and retailer pitch materials.""",
}
