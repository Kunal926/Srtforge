interface Props {
  size?: number;
}

export const BrandMark = ({ size = 28 }: Props) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <rect x="0" y="0" width="32" height="32" rx="7.5" fill="currentColor" opacity="0" />
    <g>
      <rect x="9" y="8" width="16" height="3.2" rx="1.4" fill="white" />
      <rect x="6" y="14.4" width="16" height="3.2" rx="1.4" fill="white" />
      <rect x="9" y="20.8" width="16" height="3.2" rx="1.4" fill="white" />
      <rect x="3" y="3" width="3.5" height="3.5" rx="0.8" fill="white" opacity=".75" />
    </g>
  </svg>
);
