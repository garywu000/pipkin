# Work benchmarks

Run the same six tasks against Pipkin’s Work API and grade the files in each temporary workspace. Scripted replay exercises the approvals, PDF extraction, checkpoint, and graders with predictable responses. Live mode measures an installed model through Ollama.

## Run the suite

Run these commands from the repository root on an Apple silicon Mac. The PDF case uses Pipkin’s bundled PDFKit helper.

~~~sh
python3 benchmarks/run.py --report build/benchmarks/replay.json
~~~

For a live run, start Ollama and choose a downloaded model from `ollama list`. This example selects the PDF and resume cases at 16K context.

~~~sh
python3 benchmarks/run.py --model 'your-installed-model' --context 16384 \
  --case pdf-site --case resume-work --report build/benchmarks/live.json
~~~

Omit `--case` for all six tasks. Add `--repeat 3` to take three measurements per task. Start with an idle Ollama server. Live mode checks available memory before loading, unloads the model between trials, and keeps it warm across the two phases of the resume case.

| Case | What passes |
| --- | --- |
| `pdf-site` | The page contains the facts extracted from a two-page PDF and links to both source pages. The original PDF stays intact. |
| `brief-site` | The page and checklist match the Markdown brief, their local links resolve, and the original brief stays intact. |
| `update-site` | The booking time changes and the rest of the existing page stays intact. |
| `code-fix` | The billing function has the requested expression and the adjacent notes stay intact. The checker compares Python syntax trees. |
| `resume-work` | The API stops Work after the plan is saved, restores its checkpoint, and creates the checklist while preserving the plan. |
| `denied-write` | Work requests approval, receives a decline, leaves the original workspace intact, and returns a final status. |

The runner approves only the expected file edits. Network and memory tools are disabled, and shell commands are declined. It checks HTML statically and inspects Python without executing generated files. A PASS requires verified files and the expected stop or completion events. Scripted results exercise these checks. Use live results to judge model quality and speed.

## Reading results

- **First saved output** records the earliest save receipt. **First verified output** records when a saved file first passes its checks. Both are empty for a successful declined write.
- **Wall time** includes cold model loading, document extraction, and both phases of a resumed case. The report also records model rounds, failed tools, approval counts, and the time buckets the Work API emits.
- **Resource samples** record Ollama’s reported resident model size and macOS’s system-wide free-memory percentage about every two seconds. The size covers the model reported by Ollama. Record whole-system performance separately when comparing machines.
- Keep the Mac, model digest, context, and Think setting fixed when comparing harness changes. Save individual trials so a fast success can be read alongside slower runs and failures.

Exit status `0` means every trial passed, `1` means at least one task failed its checks, and `2` means setup or transport blocked the run.

## Initial live baseline

On a 24 GiB Apple silicon Mac, the installed Qwen 27B model at 16K context passed the two-page PDF task. The first HTML save arrived at **33.5 seconds**. The page passed every check at **97.2 seconds**, and Work finished at **116.1 seconds**. It took eight model rounds and three saves. The sampled peak resident model size was 9.96 GiB. [PDF run report](baselines/qwen-27b-pdf.json)

An earlier exploratory run saved a page and failed at least one check. The pass above is a working reference for the staged runner. It takes repeated trials to measure success rate.

With the staged runner on the same PDF fixture and model, the verified page was saved in **37.8 seconds**, using one model round and one approval. This is one measured comparison. Reproduce the staged path with `--staged-site`. [Staged PDF run](baselines/qwen-27b-staged-pdf.json)

The 8B resume case initially restored `plan.txt` and returned a final answer with `checklist.txt` missing. After Pipkin kept canceled edits pending across the checkpoint and checked them before completion, the same task passed in **12.6 seconds** at 8K context. The model obtained fresh approval for the checklist and the original plan stayed intact. [Before the fix](baselines/qwen-8b-resume-before.json) · [After the fix](baselines/qwen-8b-resume-after.json)
