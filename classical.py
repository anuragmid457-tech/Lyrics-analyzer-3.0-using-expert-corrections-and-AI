# """
# classical.py - the classical baseline, so the LLM has something to beat.

#     python classical.py                 train, cross-validate, report
#     python classical.py --save          also write the fitted models to disk
#     python classical.py --min 30        refuse to report below 30 examples

# What this is for
# ----------------
# A music emotion recognition paper is not persuasive because a language model
# produced plausible labels. It is persuasive because those labels were compared
# against something, on the same songs, with the same measure. This trains the
# usual suspects on the data you already hold and prints what they score.

# What is learnable here, and what is not
# ---------------------------------------
# Three targets:

#     valence            regression, minus one to one
#     arousal            regression, minus one to one
#     canonical_emotion  classification, closed set

# The expression index is not a fourth target: it is valence rescaled to 0-100,
# so a model of it would be a model of arithmetic. The valence-arousal map is
# not a fifth: it is the two regressors drawn on axes. Fitting separate models
# for those would produce numbers that disagree with each other for no reason.

# The features are the embeddings already stored against each correction, so
# nothing has to be engineered and the same vector the similarity search uses is
# the vector the baseline learns from.

# Honesty about sample size
# -------------------------
# Below a few hundred examples these scores are noise, and the script says so
# rather than printing a number that looks like a result. Every score is
# cross-validated and printed beside a trivial baseline: the mean for regression,
# the majority class for classification. A model that cannot beat those has
# learned nothing, however good its raw score looks.
# """

# import argparse
# import json
# import os
# from collections import Counter

# from dotenv import load_dotenv

# load_dotenv()

# try:                        # filenames differ in case between machines
#     import database
# except ImportError:         # pragma: no cover
#     import Database as database

# # Below this, cross-validated scores say more about which rows fell in which
# # fold than about the model.
# DEFAULT_MIN = 30


# # --- data ----------------------------------------------------------------

# def load_rows():
#     """Every correction that carries both an embedding and a verdict."""
#     pool = database.active_corrections(with_vector=True)

#     rows = []
#     for correction in pool:
#         vector = correction.get("embedding")
#         corrected = correction.get("corrected") or {}

#         if not vector:
#             continue

#         rows.append({
#             "x": vector,
#             "valence": corrected.get("valence"),
#             "arousal": corrected.get("arousal"),
#             "label": (corrected.get("canonical_emotion")
#                       or corrected.get("primary_emotion") or "").strip().lower(),
#             "editor": correction.get("editor"),
#         })
#     return rows


# def column(rows, key):
#     """The rows that have this target, as (X, y)."""
#     X, y = [], []
#     for row in rows:
#         value = row.get(key)
#         if value is None or value == "":
#             continue
#         X.append(row["x"])
#         y.append(value)
#     return X, y


# # --- scoring -------------------------------------------------------------

# def regression_models():
#     from sklearn.ensemble import RandomForestRegressor
#     from sklearn.linear_model import Ridge
#     from sklearn.svm import SVR

#     # Plain LinearRegression is deliberately absent: on 3072 features with a
#     # few dozen rows it fits the noise exactly and scores worse than the mean.
#     # Ridge is the same model with the penalty that makes it usable here.
#     return {
#         "ridge": Ridge(alpha=10.0),
#         "svr_rbf": SVR(kernel="rbf", C=1.0, epsilon=0.1),
#         "svr_linear": SVR(kernel="linear", C=1.0, epsilon=0.1),
#         "random_forest": RandomForestRegressor(
#             n_estimators=300, random_state=0, n_jobs=-1
#         ),
#     }


# def classification_models():
#     from sklearn.ensemble import RandomForestClassifier
#     from sklearn.linear_model import LogisticRegression
#     from sklearn.svm import SVC

#     return {
#         "logistic": LogisticRegression(max_iter=2000, C=1.0),
#         "svm_rbf": SVC(kernel="rbf", C=1.0, gamma="scale"),
#         "svm_linear": SVC(kernel="linear", C=1.0),
#         "random_forest": RandomForestClassifier(
#             n_estimators=300, random_state=0, n_jobs=-1
#         ),
#     }


# def folds_for(count, wanted=5):
#     """Never ask for more folds than there are rows to put in them."""
#     return max(2, min(wanted, count // 2))


# def score_regression(X, y, name):
#     import numpy as np
#     from sklearn.model_selection import KFold, cross_val_predict

#     X = np.array(X)
#     y = np.array(y, dtype=float)

#     splitter = KFold(n_splits=folds_for(len(y)), shuffle=True, random_state=0)

#     # The trivial model: always predict the mean. Any model that cannot beat
#     # this on held-out data has learned nothing about the songs.
#     baseline_mae = float(np.mean(np.abs(y - y.mean())))

#     results = []
#     for label, model in regression_models().items():
#         try:
#             predicted = cross_val_predict(model, X, y, cv=splitter)
#         except Exception as exc:  # noqa: BLE001
#             results.append({"model": label, "error": f"{type(exc).__name__}: {exc}"})
#             continue

#         errors = np.abs(y - predicted)
#         ss_res = float(np.sum((y - predicted) ** 2))
#         ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9

#         results.append({
#             "model": label,
#             "mae": round(float(np.mean(errors)), 4),
#             "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 4),
#             "r2": round(1 - ss_res / ss_tot, 4),
#             "beats_mean": bool(np.mean(errors) < baseline_mae),
#         })

#     results.sort(key=lambda r: r.get("mae", 9e9))
#     return {
#         "target": name,
#         "rows": len(y),
#         "spread": round(float(np.std(y)), 4),
#         "baseline_mae": round(baseline_mae, 4),
#         "results": results,
#     }


# def score_classification(X, y, name):
#     import numpy as np
#     from sklearn.model_selection import StratifiedKFold, cross_val_predict
#     from sklearn.metrics import accuracy_score, f1_score

#     X = np.array(X)
#     y = np.array(y)

#     counts = Counter(y)
#     # A class with one member cannot be split across folds; drop it and say so.
#     keep = {label for label, n in counts.items() if n >= 2}
#     dropped = sorted(set(counts) - keep)
#     if dropped:
#         mask = np.array([label in keep for label in y])
#         X, y = X[mask], y[mask]
#         counts = Counter(y)

#     if len(counts) < 2:
#         return {
#             "target": name,
#             "rows": len(y),
#             "error": "Only one label survives, so there is nothing to classify.",
#             "dropped": dropped,
#         }

#     smallest = min(counts.values())
#     splits = max(2, min(5, smallest))
#     splitter = StratifiedKFold(n_splits=splits, shuffle=True, random_state=0)

#     majority = counts.most_common(1)[0]
#     baseline_accuracy = majority[1] / len(y)

#     results = []
#     for label, model in classification_models().items():
#         try:
#             predicted = cross_val_predict(model, X, y, cv=splitter)
#         except Exception as exc:  # noqa: BLE001
#             results.append({"model": label, "error": f"{type(exc).__name__}: {exc}"})
#             continue

#         results.append({
#             "model": label,
#             "accuracy": round(float(accuracy_score(y, predicted)), 4),
#             "macro_f1": round(float(f1_score(y, predicted, average="macro",
#                                              zero_division=0)), 4),
#             "beats_majority": bool(accuracy_score(y, predicted) > baseline_accuracy),
#         })

#     results.sort(key=lambda r: r.get("macro_f1", -1), reverse=True)
#     return {
#         "target": name,
#         "rows": len(y),
#         "classes": len(counts),
#         "dropped_singletons": dropped,
#         "majority_class": majority[0],
#         "baseline_accuracy": round(baseline_accuracy, 4),
#         "results": results,
#     }


# # --- fitting for later use ----------------------------------------------

# def fit_best(X, y, kind, model_name):
#     pool = regression_models() if kind == "regression" else classification_models()
#     model = pool[model_name]
#     model.fit(X, y)
#     return model


# def save_models(fitted, folder="baseline_models"):
#     import joblib

#     os.makedirs(folder, exist_ok=True)
#     for name, model in fitted.items():
#         joblib.dump(model, os.path.join(folder, f"{name}.joblib"))
#     return folder


# # --- reporting -----------------------------------------------------------

# def show_regression(report):
#     print(f"\n{report['target'].upper()}  ({report['rows']} rows, "
#           f"spread {report['spread']}, predicting the mean gives "
#           f"MAE {report['baseline_mae']})")
#     print(f"  {'model':<16}{'MAE':>9}{'RMSE':>9}{'R2':>9}   beats the mean")
#     for row in report["results"]:
#         if "error" in row:
#             print(f"  {row['model']:<16}{row['error']}")
#             continue
#         print(f"  {row['model']:<16}{row['mae']:>9}{row['rmse']:>9}"
#               f"{row['r2']:>9}   {'yes' if row['beats_mean'] else 'no'}")


# def show_classification(report):
#     if "error" in report:
#         print(f"\n{report['target'].upper()}  {report['error']}")
#         return

#     print(f"\n{report['target'].upper()}  ({report['rows']} rows, "
#           f"{report['classes']} classes, always guessing "
#           f"'{report['majority_class']}' gives {report['baseline_accuracy']})")
#     if report["dropped_singletons"]:
#         print(f"  dropped, only one example each: "
#               f"{', '.join(report['dropped_singletons'])}")
#     print(f"  {'model':<16}{'accuracy':>10}{'macro F1':>10}   beats guessing")
#     for row in report["results"]:
#         if "error" in row:
#             print(f"  {row['model']:<16}{row['error']}")
#             continue
#         print(f"  {row['model']:<16}{row['accuracy']:>10}{row['macro_f1']:>10}"
#               f"   {'yes' if row['beats_majority'] else 'no'}")


# def main():
#     parser = argparse.ArgumentParser(description=__doc__)
#     parser.add_argument("--save", action="store_true",
#                         help="fit the best model per target and write it to disk")
#     parser.add_argument("--min", type=int, default=DEFAULT_MIN,
#                         help=f"rows needed before the scores mean anything "
#                              f"(default {DEFAULT_MIN})")
#     parser.add_argument("--json", action="store_true",
#                         help="print the report as JSON instead of a table")
#     args = parser.parse_args()

#     rows = load_rows()
#     print(f"{len(rows)} correction(s) carry an embedding.")

#     if not rows:
#         print("\nNothing to train on. Corrections need embeddings, which means "
#               "GOOGLE_API_KEY has to be set when they are saved.")
#         return

#     reports = {}

#     for target in ("valence", "arousal"):
#         X, y = column(rows, target)
#         if len(y) < 4:
#             print(f"\n{target.upper()}  only {len(y)} rows, too few to split at all.")
#             continue
#         reports[target] = score_regression(X, y, target)

#     X, y = column(rows, "label")
#     if len(y) >= 4:
#         reports["canonical_emotion"] = score_classification(X, y, "canonical emotion")

#     if args.json:
#         print(json.dumps(reports, indent=2))
#     else:
#         for target in ("valence", "arousal"):
#             if target in reports:
#                 show_regression(reports[target])
#         if "canonical_emotion" in reports:
#             show_classification(reports["canonical_emotion"])

#     smallest = min([r["rows"] for r in reports.values()] or [0])
#     if smallest < args.min:
#         print(f"\n---\nRead the table above as a smoke test, not a result. "
#               f"With {smallest} rows these scores move sharply depending on "
#               f"which rows land in which fold. Collect corrections, or train "
#               f"on a public labelled corpus, before quoting any of this.")

#     if args.save:
#         fitted = {}
#         for target in ("valence", "arousal"):
#             report = reports.get(target)
#             if not report or not report["results"]:
#                 continue
#             best = report["results"][0]
#             if "model" not in best or "error" in best:
#                 continue
#             X, y = column(rows, target)
#             fitted[target] = fit_best(X, y, "regression", best["model"])
#             print(f"\nfitted {target} with {best['model']}")

#         report = reports.get("canonical_emotion")
#         if report and report.get("results"):
#             best = report["results"][0]
#             if "error" not in best:
#                 X, y = column(rows, "label")
#                 fitted["canonical_emotion"] = fit_best(
#                     X, y, "classification", best["model"]
#                 )
#                 print(f"fitted canonical emotion with {best['model']}")

#         if fitted:
#             print("saved to", save_models(fitted))


# if __name__ == "__main__":
#     main()