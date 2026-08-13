(() => {
  const toggle = document.querySelector("[data-admin-menu-toggle]");
  const sidebar = document.getElementById("adminSidebar");
  const backdrop = document.querySelector("[data-admin-sidebar-close]");
  if (!toggle || !sidebar || !backdrop) return;
  if (window.matchMedia("(max-width: 768px)").matches) {
    sidebar.setAttribute("aria-hidden", "true");
  }

  const setOpen = (open) => {
    document.body.classList.toggle("adminNavOpen", open);
    toggle.setAttribute("aria-expanded", String(open));
    sidebar.setAttribute("aria-hidden", String(!open));
    if (open) {
      sidebar.querySelector("a")?.focus();
    } else {
      toggle.focus();
    }
  };

  toggle.addEventListener("click", () => {
    setOpen(!document.body.classList.contains("adminNavOpen"));
  });
  backdrop.addEventListener("click", () => setOpen(false));
  sidebar.addEventListener("click", (event) => {
    if (event.target.closest("a") && window.matchMedia("(max-width: 768px)").matches) {
      setOpen(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("adminNavOpen")) {
      setOpen(false);
    }
  });
  window.addEventListener("resize", () => {
    if (!window.matchMedia("(max-width: 768px)").matches) {
      document.body.classList.remove("adminNavOpen");
      toggle.setAttribute("aria-expanded", "false");
      sidebar.removeAttribute("aria-hidden");
    }
  });
})();
