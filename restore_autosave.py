import re

file_path = "app/templates/index.html"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Remove data-nav-icon="..."
content = re.sub(r'\s+data-nav-icon="[^"]+"', '', content)

# Restore autoSaveResultCard and autoSaveSection
auto_save_script = """
      async function autoSaveResultCard(card) {
        if (!IS_LOGGED_IN) return;
        const itemId = card.dataset.passageId;
        if (resultSavedKeys.has(itemId)) return;
        resultSavedKeys.add(itemId);
        try {
          const clone = card.cloneNode(true);
          clone.querySelectorAll(".resultSaveBar").forEach((el) => el.remove());
          const title = (clone.querySelector(".source")?.textContent || "").trim();
          const excerpt = (clone.querySelector(".pali")?.textContent || "").trim();
          await postSavedItem("result", title, excerpt, clone.outerHTML);
        } catch (error) {
          resultSavedKeys.delete(itemId);
        }
      }

      async function autoSaveSection(viewer) {
        if (!IS_LOGGED_IN || !activeSectionId) return;
        if (sectionSavedKeys.has(activeSectionId)) return;
        sectionSavedKeys.add(activeSectionId);
        try {
          const title = (viewer.querySelector("h2")?.textContent || "").trim();
          const excerpt = (viewer.querySelector(".pali")?.textContent || "").trim();
          await postSavedItem("section", title, excerpt, viewer.outerHTML);
        } catch (error) {
          sectionSavedKeys.delete(activeSectionId);
        }
      }
"""

if "async function autoSaveResultCard" not in content:
    # Insert before manualSaveResultCard
    content = content.replace("async function manualSaveResultCard", auto_save_script + "\n      async function manualSaveResultCard")

# Now ensure they are called when things are loaded:
# In translateCard and summarizeExcerptCard (for autoSaveResultCard)
if "autoSaveResultCard(card);" not in content:
    content = content.replace(
        'renderTranslation(card, payload.translation || {});',
        'renderTranslation(card, payload.translation || {});\n          autoSaveResultCard(card);'
    )
    content = content.replace(
        'if (body) body.innerHTML = `<div class="notice">${tr("results.excerptSummaryEmpty")}</div>`;\n        }',
        'if (body) body.innerHTML = `<div class="notice">${tr("results.excerptSummaryEmpty")}</div>`;\n        }\n          autoSaveResultCard(card);'
    )
    # Wait, the summary logic has two places it can succeed or fail, and finally block.
    # Actually, it's safer to just put it at the end of the `try` block. I'll just use a simpler replace.

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
