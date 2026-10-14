# Archer AI composer — design QA

final result: passed

Scope: the reference-inspired AI conversation composer within the implemented search flow.
The user requested something like the supplied screenshot, adapted to Archer's real tools.

## Evidence and normalization

- Source: `C:/Users/Jash/AppData/Local/Temp/codex-clipboard-92b4c55f-8a8a-4dde-9f65-2dd3cc733013.png`, 985 × 150 pixels.
- Source component bounds: approximately x=13…934, y=10…132, or 921 × 122 pixels. The supplied image is a component crop; the surrounding application viewport is unknown.
- Implementation: `screenshots/ai-composer-context.png`, captured through Codex's in-app browser at a requested 1010 × 900 CSS viewport. The capture is 995 × 886 pixels; crop coordinates were normalized by those measured ratios, rather than assuming screenshot pixels equal CSS pixels.
- Implementation component: approximately 858 × 132 CSS pixels. The normalized raster crop is `screenshots/ai-composer-desktop.png`.
- Combined focused comparison: `screenshots/composer-comparison.png`. The reference component is scaled uniformly to the implementation crop width; both are in the same comparison image.
- Mobile context: `screenshots/ai-composer-mobile.png`, requested 390 × 844 CSS viewport. DOM measurement confirms no document-level horizontal overflow.
- State: enabled follow-up composer with text, Research selected, send enabled; dark theme. The source text and model labels intentionally differ to reflect Archer's actual product.
- Full-view context was inspected in the in-app browser; the focused comparison is needed because the reference covers only the composer, and its toolbar is small within the whole search page.

## Findings

No actionable P0/P1/P2 differences remain for the requested adaptation.

- **Fonts and typography:** Archer's existing self-hosted DM Sans is retained. The textarea is 16px with a 1.55 line height; toolbar text is smaller and muted. The reference uses similar neutral sans-serif hierarchy. Wrapping and the editable cursor were checked. Mobile retains 16px entry text and hides the long model label to preserve control space.
- **Spacing and layout rhythm:** both use a spacious text area over a compact lower toolbar, generous rounded corners, and a right-aligned circular send control. Archer is about 10 CSS pixels taller to support two lines and toolbar padding. This is an intentional adaptation rather than a fidelity defect.
- **Colors and tokens:** the #282828 composer, subtle #353535 border, light text and muted toolbar match the reference's charcoal treatment while fitting the existing dark result cards. The send action uses a light circular surface.
- **Asset fidelity:** the reference contains only UI controls; no raster assets are required. Archer reuses its existing vector icon symbols for source selection, tools and the upward send affordance. The upward chevron differs slightly from the reference's shafted arrow; this is acceptable P3 polish. No fake attachment, voice or approval control was introduced.
- **Copy and content:** actual configured model, Research/Quick answer depth, Archer tools and source controls replace the reference's model, permission and microphone affordances. They correspond to implemented behavior. No text from the user's instruction appears as product copy.
- **Responsive use:** the mobile composer wraps text, keeps the source control and depth selector visible, and keeps the send button within bounds. The model name is intentionally hidden at narrow widths. Existing comparison tables scroll within their own region.

## Interaction checks

- Typing enables send; empty text disables it.
- Shift+Enter inserts a newline; Enter submits a research follow-up and visibly enters the gathering/planning state.
- The source control opens evidence inspection/selection.
- The native depth selector provides Research and Quick answer.
- The research flow reaches completion with native-tool calculation cards and a cited comparison table.
- Earlier offline Chromium coverage passed consent, no paid GETs, comparison selection, editable calculators, follow-ups, stop, saved Balanced, utility suppression and reduced motion.
- In-app browser error log was empty during the composer checks.

## Comparison history

The first combined image showed a screenshot-crop mismatch: the right edge was clipped because the browser capture's measured raster size differed from the requested CSS viewport. Full-page visual inspection confirmed the actual composer had its right padding and rounded corner. The capture was regenerated from the full viewport image using measured density ratios. The revised combined evidence shows the full send control and right edge. No application layout change was required for that capture issue.

## Implementation checklist

- Multiline input, source selection, depth and send: complete.
- Keyboard submission, disabled state and browser console: checked.
- Desktop focused comparison and mobile context: captured and inspected.
- Real provider verification remains separate from visual QA and requires the user's API key.

## Follow-up polish

- P3: use a matching shafted arrow if Archer later consolidates its icon library. The current upward affordance is clear and functional.
