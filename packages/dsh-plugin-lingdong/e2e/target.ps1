# A GUI target window for the end-to-end comparison.
#
# Presents one random token in a way the caller chooses, so a task can ask the
# agent to read something it can only get from the screen:
#
#   -Mode text    the token sits in a text box (reachable through the
#                 accessibility tree, no pixels required)
#   -Mode pixels  the token is painted on a canvas (pixels only)
#
# The window is titled "LD Target" so both providers can address it by name.
param(
  [Parameter(Mandatory = $true)][string]$Token,
  [ValidateSet('text', 'pixels')][string]$Mode = 'text'
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = 'LD Target'
$form.Width = 640
$form.Height = 420
$form.StartPosition = 'Manual'
$form.Left = 320
$form.Top = 220
$form.BackColor = [System.Drawing.Color]::White

if ($Mode -eq 'text') {
  $label = New-Object System.Windows.Forms.Label
  $label.Text = 'The token is:'
  $label.Left = 20
  $label.Top = 20
  $label.Width = 300
  $label.Height = 24
  $form.Controls.Add($label)

  $box = New-Object System.Windows.Forms.TextBox
  $box.Name = 'TokenBox'
  $box.Text = $Token
  $box.Left = 20
  $box.Top = 50
  $box.Width = 560
  $box.Height = 32
  $box.Font = New-Object System.Drawing.Font('Consolas', 16)
  $box.ReadOnly = $true
  $form.Controls.Add($box)
}
else {
  $canvas = New-Object System.Windows.Forms.PictureBox
  $canvas.Left = 0
  $canvas.Top = 0
  $canvas.Width = 640
  $canvas.Height = 420
  $canvas.BackColor = [System.Drawing.Color]::Black
  $canvas.Add_Paint({
    param($sender, $event)
    $g = $event.Graphics
    $g.Clear([System.Drawing.Color]::Black)
    $font = New-Object System.Drawing.Font('Consolas', 40, [System.Drawing.FontStyle]::Bold)
    $brush = [System.Drawing.Brushes]::White
    $g.DrawString($Token, $font, $brush, 30, 150)
  })
  $form.Controls.Add($canvas)
}

[void]$form.ShowDialog()
