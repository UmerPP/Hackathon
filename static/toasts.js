for (const toast of document.querySelectorAll(".toast")) {
    const close = toast.querySelector("button");
    close.addEventListener("click", () => toast.remove());
    if (window.matchMedia("(prefers-reduced-motion: no-preference)").matches) {
        toast.animate([{opacity: 0, transform: "translateY(8px)"}, {opacity: 1, transform: "none"}], 180);
    }
}
