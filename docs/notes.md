# ProvenClause notes

Stage 0 notes: paper baselines, data statistics, and reading notes on 5 NDAs. Date: 2026-10-21.

---

## 1. Baselines and evaluation setup of the original paper

**Paper:** Koreeda & Manning, "ContractNLI: A Dataset for Document-level Natural Language Inference for Contracts", Findings of EMNLP 2021 (arXiv 2110.01799). The numbers come from Table 4 and Table 3 of the paper.

### 1.1 Baseline numbers (full test set, 123 contracts)

| System | Accuracy | F1 (Contradiction) | F1 (Entailment) | Evidence mAP |
|---|---|---|---|---|
| Majority class (outputs the most common finding for each checklist question) | .674 | .083 | .428 | — |
| Doc TF-IDF + SVM | .733 | .197 | .641 | — |
| Span NLI BERT-base | .838 | .287 | .765 | .885 |
| **Span NLI BERT-large** | **.875** | **.357** | **.834** | **.922** |
| DeBERTa v2 xlarge + CUAD pretraining (best result in the paper) | .892 | .405 | .859 | .936 |

Detailed design Section 8.1 says "BERT-large accuracy about 87.5%, Contradiction F1 about 0.36". We checked these numbers, and they are correct.

### 1.2 Evaluation setup: 4 differences from our setup

1. **Averaging method.** The paper first micro-averages over contracts, then macro-averages over checklist questions.
   - Accuracy: each checklist question has the same number of items, so the macro-averaged accuracy is equal to the accuracy over all items together. The two methods give the same result.
   - F1 (Contradiction): the two methods give different results. The paper calculates F1 for each of the 17 checklist questions, then takes the mean. In test, 7 checklist questions have no Contradiction item (nda-8, 10, 11, 12, 13, 15, 18), and 4 more have only 1–4 items. The paper does not say clearly how it calculates F1 when there is no Contradiction item.
   - **Decided on 2026-10-21:** the main table uses the pooled calculation. We also calculate the paper-style macro-average, but only to compare with the paper (see Technical decisions Section 3.2).
2. **Different test set.** The paper uses the full test set of 123 contracts. Our formal experiments use test-40. To compare with the paper, use the extra A2-F result on the full test set.
3. **The results are averages of many runs.** The paper runs 10 hyperparameter settings and reports the mean of the 3 best models on dev.
4. **Different evidence metrics.** The mAP and P@R80 of the paper are ranking metrics, and they need a score for each sentence. Our system outputs a set of sentences, so we calculate precision and recall. You cannot compare the two directly.

### 1.3 Other useful facts

- **Different annotation methods.** For train, annotators checked only the annotated sentences and did not read the full text. For dev, the main annotator did most of the annotation alone. For test, the main annotator read most full texts and added missing labels. So train evidence can have more missing labels.
- **With correct evidence, Contradiction F1 increases clearly.** Table 6 does only the Entailment / Contradiction binary classification: the F1 (Contradiction) of BERT-base increases from .490 to .657. This supports our design: "find the correct evidence first, then give the finding".
- **The paper appendix does not publish the annotation guidelines for each checklist question.** The appendix lists only the 17 checklist questions. So we wrote the `guidance` in `nda_17.yaml` from the text of the checklist questions and the evidence examples in train.
- **Different numbering.** Table 10 of the paper numbers the questions 1–17. The dataset IDs are nda-1 to nda-20 (some numbers are not used). The dataset spells the name of nda-2 as "None-inclusion", and the paper spells it "Non-inclusion". `nda_17.yaml` copies the dataset.

---

## 2. Data statistics

Command: `uv run python -m provenclause.eval.stats`

| Split | Contracts | Mean characters | Max characters | Mean sentences | Entailment | Contradiction | NotMentioned |
|---|---|---|---|---|---|---|---|
| train | 423 | 11,049 | 54,571 | 77.8 | 3,530 | 841 | 2,820 |
| dev | 61 | 12,095 | 32,359 | 83.6 | 519 | 95 | 423 |
| test | 123 | 11,218 | 41,779 | 81.8 | 968 | 220 | 903 |
| test-40 | 40 | 13,848 | 41,779 | 95.4 | 294 | 66 | 320 |

**Bias of test-40 (seed 20261021):**
- On average, test-40 contracts are 23% longer than test contracts (13,848 vs 11,218 characters). The longest contract in the full test set is also in test-40. So estimate the cost of the formal experiments from this length.
- 14 of the 40 contracts are in the longest 25% of test. The expected value for a random sample is 10. So there are more samples for hard case H5 (long contracts).
- Format distribution: search-pdf 27, sec-text 8, sec-html 5. The ratio in the full test set is 76 : 24 : 23. sec-html is under-represented.
- Finding distribution: Entailment 43%, Contradiction 9.7%, NotMentioned 47%. The full test set has 46%, 10.5%, 43%.
- The paper splits the data with stratification by format. Our sample has no stratification. We write these biases in the README and do not sample again. To sample again is to choose data while you look at test.

---

## 3. Reading notes on 5 dev NDAs

The 5 contracts cover 3 formats, and their lengths go from the shortest to the longest:

| ID | Format | Characters | Sentences |
|---|---|---|---|
| 456 | sec-text | 2,270 (shortest in dev) | 20 |
| 411 | sec-text | 9,425 | 46 |
| 3 | search-pdf | 11,142 | 97 |
| 563 | sec-html | 13,208 | 80 |
| 64 | search-pdf | 32,359 (longest in dev) | 255 |

### 3.1 Section numbering

- **Forms we saw:** `1.`–`22.` (all 5 contracts); `A.`, `B.`; `(a)`, `(i)`, `(A)`, `(1)`; `a)`, `1)` with only a right parenthesis (contract 3); headings with no number, for example `WHEREAS`, `RECITALS:`, `General`.
- **Forms we did not see:** `1.1`, `Article III`, top-level sections with Roman numerals. The stage 2 regular expressions must still support them, but take the test cases from train.
- **How headings and body text split:**
  - A heading on its own line is a separate sentence, for example 411 S9 `1. Non-Competition.`.
  - A heading and body text on the same line are in the same sentence, for example 64 S44 `A. NO REPRESENTATION OR WARRANTIES: The Receiving Party agrees…`.
  - A heading can split into 2 sentences, for example 64 S115–S116 `7. ENVIRONMENTAL` / `DOCUMENTATION AND ISSUES:`.
- **How lists split:** each list item is one sentence. The lead-in sentence is a separate sentence. A half sentence after the list joins the last item. For example, 411 S12 `(b)` contains the full non-compete rule.

### 3.2 Definitions

- **Forms:** `"X" shall mean`, `"X" means`, `(the "Agreement")`, `(hereinafter referred to as the "Agreement")`, `(collectively, "Representatives")`, `is referred to as "Buyer"`, `is hereinafter collectively referred to as the "Information"`.
- **Negative definitions:** `The term "Confidential Information" does not include` (411 S18). This type of sentence starts an exception list. L2 must treat it as a definition and also as an exception.
- **Quotation marks:** some contracts use straight quotes, some use curly quotes, and contract 64 uses both. The regular expression must match both `"` and the curly quotes (U+201C and U+201D).
- **Number of definitions in each contract (counted by hand, error about ±2):** 456 has 0, 411 has 12, 3 has 11, 563 has 9, 64 has 24. The definitions of contract 456 are on a different page, not in the text.
- **Trap:** 563 S65 uses "Receiving Party", but the contract defines only "Recipient". When `lookup_definition` finds nothing, it must return "not defined" and must not raise an error.

### 3.3 Cross-references

- **Forms that point back to a section of the same contract:** `this Section 1`, `Section 2(a) hereof`, `Except as provided in Section 3`, `Paragraph 4E` (no separator between the number and the letter), `Paragraph 6K above`.
- **Forms that point outside the contract and cannot be resolved:**
  - `Section 12 of the Securities Exchange Act of 1934` (411 S15): a statute. The regular expression will match it by mistake, so exclude forms like `of the ... Act`.
  - `Section A of this Attachment A` (456 S2): this section is not in the text.
  - `as that term is defined in the Merger Agreement` (411 S30): a different document.
- **References with no number:** `the foregoing`, `the immediately preceding sentence`, `this paragraph`, `as provided above`, `hereof`. L2 does not handle them. It handles only numbered references.
- **Forms we did not see:** `clause 4.2`, `Article II`.

### 3.4 Exception clauses

- **Words in the detailed design list:** `except`, `provided that` / `provided, however`, `unless`, `notwithstanding`, and `other than` all occur. `save that` does not occur. `subject to` is sometimes not an exception (3 S13, 563 S65).
- **Exception forms not in the list (we suggest that stage 2 discusses whether to add them to L2):**
  - `shall not apply to`, `does not include`, `is not subject to the terms of this Agreement if`
  - `Nothing contained in ... shall be deemed to prevent`, `However, this paragraph shall not prohibit`
  - `without the prior written consent of`, `Absent ... prior written consent`
  - `so long as`, `to the extent necessary`, `only if`
- **Where exceptions occur:**
  - A short exception is in the same sentence as the main rule, for example 563 S23 `except Recipient's Representatives`.
  - An exclusion list is in a separate section, with one sentence for each item, for example Section 2 "Exclusions" of contract 3 (S26–S31).
  - Some exceptions are in the next sentence or the next numbered paragraph, for example 411 S13–S15. So L2 must check "the other sentences in the same section". It is not sufficient to check only the evidence sentences.

### 3.5 Text extraction problems

- **Not present in any of the 61 dev contracts:** line-break hyphens (`employ-ees`), ligatures (ﬁ), line breaks inside a sentence, separate page-number lines. The only text that no sentence covers is newline characters.
- **Present:**
  - Extra letters: 64 S29 `SuchF information`, 64 S80 `theF Indemnifying Party`.
  - Underscores and ellipses for blanks to fill in: 64 S130–S241, 3 S3.
  - Headers and footers mixed into the body text: 456 S0 `(page 2.)`, 563 S0 `Exhibit 99(d)(7)`, 64 S242–S254.
  - Sentences that start in the middle: PDF line breaks became sentence boundaries, for example 64 S4–S5 `This Commercial` / `CONFIDENTIALITY AND NON-DISCLOSURE AGREEMENT…`. So section detection must not assume that each sentence starts at the start of a real sentence.

### 3.6 How Contradiction is expressed

| Contract | Contradiction checklist items (evidence sentences) |
|---|---|
| 456 | None |
| 411 | nda-2 [17], nda-5 [17], nda-7 [17] |
| 3 | nda-2 [17] |
| 563 | nda-1 [12, 13], nda-2 [12–15] |
| 64 | None |

- **nda-5 and nda-7 of 411 (sharing with employees and with third parties):** S17 says that the shareholder "shall not disclose" the confidential information to any person. The only exceptions in the contract are the exclusions in the definition (S18–S23) and disclosure required by law (S25–S26). No exception permits sharing. **Here the Contradiction comes from "no exception", not from an exception word.** The keyword rules of L2 cannot find this case.
- **nda-2 (includes only technical information):** the definition lists business information, so the finding is Contradiction. For example, 3 S17: `"Confidential Information" shall mean scientific, research, technical or business information`.

---

## 4. Items for you to confirm

1. **I chose `risk_label_for_recipient`.** The rule is: select the finding that is worse for the receiving party. For example, nda-1 selects Contradiction, because information with no marking is also confidential information, so the receiving party must protect a larger scope. It affects only the order of the report, not the scores. Please read [`checklists/nda_17.yaml`](../checklists/nda_17.yaml).
2. **The F1 (Contradiction) calculation** (Section 1.2, point 1): decided on 2026-10-21.
3. **The L2 exception word list** (Section 3.4): discuss it in stage 4. Stage 0 does not change the detailed design.
