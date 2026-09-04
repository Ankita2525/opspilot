export function SafetyCallout() {
  return (
    <aside className="safety-callout" aria-label="Safety notice">
      <span className="safety-callout-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none">
          <path
            d="M12 3l7 3v5c0 4.5-2.9 8.4-7 9.5C7.9 19.4 5 15.5 5 11V6l7-3z"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
          <path
            d="M9.5 12.2l1.7 1.7 3.4-3.6"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <p className="safety-callout-text">
        OpsPilot runs in isolated, ephemeral environments with read-only access
        unless remediation is explicitly approved.
      </p>
    </aside>
  );
}
