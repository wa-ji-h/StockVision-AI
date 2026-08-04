document.addEventListener("DOMContentLoaded", () => {
    // Menu déroulant du profil
    const profileBtn = document.getElementById("profileBtn");
    const profileDropdown = document.getElementById("profileDropdown");

    if (profileBtn && profileDropdown) {
        profileBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            profileDropdown.classList.toggle("open");
        });

        document.addEventListener("click", (e) => {
            if (!profileDropdown.contains(e.target) && e.target !== profileBtn) {
                profileDropdown.classList.remove("open");
            }
        });
    }

    // Collapse du sidebar
    const collapseBtn = document.getElementById("collapseBtn");
    const sidebar = document.getElementById("dashSidebar");

    if (collapseBtn && sidebar) {
        collapseBtn.addEventListener("click", () => {
            sidebar.classList.toggle("collapsed");
        });
    }
});