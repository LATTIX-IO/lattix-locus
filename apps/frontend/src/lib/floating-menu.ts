export type FloatingMenuPositionOptions = {
  anchorX: number;
  anchorY: number;
  menuHeight: number;
  menuWidth: number;
  viewportHeight: number;
  viewportPadding?: number;
  viewportWidth: number;
};

type FloatingMenuPosition = {
  left: number;
  top: number;
};

function clamp(value: number, min: number, max: number): number {
  if (max <= min) {
    return min;
  }
  return Math.min(Math.max(value, min), max);
}

export function resolveFloatingMenuPosition({
  anchorX,
  anchorY,
  menuHeight,
  menuWidth,
  viewportHeight,
  viewportPadding = 12,
  viewportWidth,
}: FloatingMenuPositionOptions): FloatingMenuPosition {
  const maxLeft = Math.max(viewportPadding, viewportWidth - menuWidth - viewportPadding);
  const maxTop = Math.max(viewportPadding, viewportHeight - menuHeight - viewportPadding);

  return {
    left: clamp(anchorX, viewportPadding, maxLeft),
    top: clamp(anchorY, viewportPadding, maxTop),
  };
}