import {
  Accessibility, Battery, Bot, Camera, Clipboard, Clock, Command, Crosshair,
  Database, FileCode, Focus, GitBranch, Globe, Hourglass, Image, Layout,
  ListChecks, ListRestart, Lock, LogOut, MessageSquare, Monitor, MonitorDown,
  MonitorOff, Moon, MousePointer, MousePointer2, MoveHorizontal, Palette,
  Percent, Play, Power, RefreshCw, Rocket, RotateCw, Scan, ScanLine, Search,
  Settings, ShieldCheck, Square, Sun, Terminal, Trash2, Type, UserCheck,
  Variable, Video, Volume2, VolumeX, Wifi, Wrench, XCircle, Zap,
  type LucideIcon,
} from 'lucide-react';

const NODE_ICONS: Record<string, LucideIcon> = {
  Accessibility, Battery, Bot, Camera, Clipboard, Clock, Command, Crosshair,
  Database, FileCode, Focus, GitBranch, Globe, Hourglass, Image, Layout,
  ListChecks, ListRestart, Lock, LogOut, MessageSquare, Monitor, MonitorDown,
  MonitorOff, Moon, MousePointer, MousePointer2, MoveHorizontal, Palette,
  Percent, Play, Power, RefreshCw, Rocket, RotateCw, Scan, ScanLine, Search,
  Settings, ShieldCheck, Square, Sun, Terminal, Trash2, Type, UserCheck,
  Variable, Video, Volume2, VolumeX, Wifi, Wrench, XCircle, Zap,
};

export function getNodeIcon(name: string): LucideIcon {
  return NODE_ICONS[name] || Zap;
}
