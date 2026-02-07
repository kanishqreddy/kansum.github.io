const memories = document.querySelectorAll(".memory");

const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add("seen");
    }
  });
}, { threshold: 0.6 });

memories.forEach(m => observer.observe(m));
