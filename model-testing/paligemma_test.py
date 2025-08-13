#!/usr/bin/env python3
import os
import argparse
from pathlib import Path
import torch
from transformers import PaliGemmaProcessor, PaliGemmaForConditionalGeneration
from transformers.image_utils import load_image

def choose_dtype():
    if torch.cuda.is_available():
        major, _ = torch.cuda.get_device_capability()
        return torch.bfloat16 if major >= 8 else torch.float16
    return torch.float32

def iter_images(root_dir: Path):
    for label_name, y in (("positive", 1), ("negative", 0)):
        d = root_dir / label_name
        if not d.exists():
            continue
        for p in sorted(d.glob("*")):
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                yield str(p), y

def first_token_ids(tok, word: str):
    variants = [word, word.capitalize(), f" {word}", f" {word.capitalize()}"]
    ids = set()
    for v in variants:
        seq = tok.encode(v, add_special_tokens=False)
        if seq:
            ids.add(seq[0])
    return sorted(ids)

def score_yes_no(model, inputs, yes_ids, no_ids):
    """Return (p_yes, p_no, yes_ratio)."""
    with torch.inference_mode():
        out = model(**inputs)                 # single forward
        logits = out.logits[:, -1, :]         # next-token distribution
        probs = torch.softmax(logits, dim=-1)

    yes_ids_t = torch.tensor(yes_ids, device=probs.device, dtype=torch.long)
    no_ids_t  = torch.tensor(no_ids,  device=probs.device, dtype=torch.long)

    p_yes = float(probs.index_select(dim=-1, index=yes_ids_t).sum().item())
    p_no  = float(probs.index_select(dim=-1, index=no_ids_t).sum().item())
    denom = max(p_yes + p_no, 1e-9)
    yes_ratio = p_yes / denom
    return p_yes, p_no, yes_ratio
def wrap_prompt_with_image_token(q: str) -> str:
    # Your script feeds exactly one image per call.
    # If you later batch multiple images per text, repeat "<image>" that many times.
    return f"answer en <image> In this single frame, {q} Answer yes or no."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model_id", type=str, default="google/paligemma2-3b-mix-448")
    ap.add_argument("--images_dir", type=str, default="test_frames")
    ap.add_argument("--question", type=str, default="Is someone taking a jump shot?")
    ap.add_argument("--min_conf", type=float, default=0.55,  # now interpreted as YES ratio threshold
                    help="Minimum YES confidence ratio to predict positive (1). Else predict negative (0).")
    ap.add_argument("--show_scores", action="store_true", help="Print p_yes, p_no, yes_ratio per image.")
    ap.add_argument("--verbose_raw", action="store_true", help="Also print raw generation (debug).")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype  = choose_dtype()
    print(f"[info] device={device} dtype={dtype} model_id={args.model_id}")

    HF_TOKEN = os.getenv("HF_TOKEN", None)

    model = PaliGemmaForConditionalGeneration.from_pretrained(
        args.model_id, torch_dtype=dtype, device_map="auto" if device=="cuda" else None, token=HF_TOKEN
    ).eval()
    processor = PaliGemmaProcessor.from_pretrained(args.model_id, token=HF_TOKEN)

    prompt = wrap_prompt_with_image_token(args.question)

    root = Path(args.images_dir)
    items = list(iter_images(root))
    if not items:
        print(f"[warn] No images found in {root}/positive or {root}/negative")
        return

    yes_ids = first_token_ids(processor.tokenizer, "yes")
    no_ids  = first_token_ids(processor.tokenizer, "no")
    if not yes_ids or not no_ids:
        raise RuntimeError("Could not derive tokenizer IDs for 'yes'/'no'.")

    tp = tn = fp = fn = 0

    print("\n=== Per-image results ===")
    for path, y_true in items:
        try:
            image = load_image(path)
            inputs = processor(text=prompt, images=image, return_tensors="pt")
            inputs = {k: v.to(model.device) for k, v in inputs.items()}
            if "pixel_values" in inputs and inputs["pixel_values"].dtype != dtype:
                inputs["pixel_values"] = inputs["pixel_values"].to(dtype)

            p_yes, p_no, yes_ratio = score_yes_no(model, inputs, yes_ids, no_ids)

            # NEW decision rule:
            # Predict positive (1) iff YES confidence ratio meets threshold; else negative (0).
            y_pred = 1 if yes_ratio >= args.min_conf else 0
            yn = "yes" if y_pred == 1 else "no"

            line = f"{'P' if y_true else 'N'} | {os.path.basename(path):<30} | pred={yn}"
            if args.show_scores:
                line += f" | p_yes={p_yes:.3f} p_no={p_no:.3f} yes_ratio={yes_ratio:.2f} thr={args.min_conf:.2f}"
            if args.verbose_raw:
                input_len = inputs["input_ids"].shape[-1]
                with torch.inference_mode():
                    g = model.generate(**inputs, max_new_tokens=4, do_sample=False)
                raw = processor.decode(g[0][input_len:], skip_special_tokens=True).strip()
                line += f" | raw='{raw}'"
            print(line)

            # Metrics
            if y_true == 1 and y_pred == 1: tp += 1
            elif y_true == 0 and y_pred == 0: tn += 1
            elif y_true == 0 and y_pred == 1: fp += 1
            elif y_true == 1 and y_pred == 0: fn += 1

        except Exception as e:
            print(f"[error] {path}: {e}")

    total = tp + tn + fp + fn
    print("\n=== Summary (thresholded YES detector) ===")
    print(f"TP={tp}  TN={tn}  FP={fp}  FN={fn}")
    if total > 0:
        acc  = (tp + tn) / total
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec  = tp / (tp + fn) if (tp + fn) else 0.0
        f1   = (2*prec*rec)/(prec+rec) if (prec+rec) else 0.0
        print(f"Accuracy:  {acc:.3f}\nPrecision: {prec:.3f}\nRecall:    {rec:.3f}\nF1:        {f1:.3f}")
    else:
        print("No samples evaluated.")

if __name__ == "__main__":
    main()
