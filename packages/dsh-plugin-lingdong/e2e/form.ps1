# A GUI form for the end-to-end comparison.
#
# Six different kinds of interaction have to land before the oracle can pass:
# typing into a field, choosing from a drop-down, ticking a check box, selecting
# a radio button, and pressing the button that writes the result. Every default
# is deliberately wrong, so a form that is only looked at cannot pass.
#
#   -Out <path>   where the Submit button writes the collected values as JSON
param(
  [Parameter(Mandatory = $true)][string]$Out
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = 'LD Form'
$form.Width = 620
$form.Height = 420
$form.StartPosition = 'Manual'
$form.Left = 300
$form.Top = 180
$form.Font = New-Object System.Drawing.Font('Segoe UI', 11)
$form.WindowState = 'Normal'

function Add-Label($text, $top) {
  $label = New-Object System.Windows.Forms.Label
  $label.Text = $text
  $label.Left = 24
  $label.Top = $top
  $label.Width = 150
  $form.Controls.Add($label)
}

Add-Label 'Name' 24
$nameBox = New-Object System.Windows.Forms.TextBox
$nameBox.Name = 'NameBox'
$nameBox.Left = 190
$nameBox.Top = 20
$nameBox.Width = 380
$nameBox.Text = ''
$form.Controls.Add($nameBox)

Add-Label 'City' 74
$city = New-Object System.Windows.Forms.ComboBox
$city.Name = 'CityBox'
$city.Left = 190
$city.Top = 70
$city.Width = 380
$city.DropDownStyle = 'DropDownList'
[void]$city.Items.AddRange(@('Beijing', 'Chengdu', 'Guangzhou', 'Shanghai'))
$city.SelectedIndex = 0
$form.Controls.Add($city)

$subscribe = New-Object System.Windows.Forms.CheckBox
$subscribe.Name = 'SubscribeBox'
$subscribe.Text = 'Subscribe to the newsletter'
$subscribe.Left = 190
$subscribe.Top = 120
$subscribe.Width = 380
$subscribe.Checked = $false
$form.Controls.Add($subscribe)

$planA = New-Object System.Windows.Forms.RadioButton
$planA.Name = 'PlanA'
$planA.Text = 'Plan A'
$planA.Left = 190
$planA.Top = 165
$planA.Width = 160
$planA.Checked = $true
$form.Controls.Add($planA)

$planB = New-Object System.Windows.Forms.RadioButton
$planB.Name = 'PlanB'
$planB.Text = 'Plan B'
$planB.Left = 370
$planB.Top = 165
$planB.Width = 160
$form.Controls.Add($planB)

$submit = New-Object System.Windows.Forms.Button
$submit.Name = 'SubmitButton'
$submit.Text = 'Submit'
$submit.Left = 190
$submit.Top = 225
$submit.Width = 140
$submit.Height = 40
$submit.Add_Click({
  $payload = [ordered]@{
    name      = $nameBox.Text
    city      = $city.SelectedItem
    subscribe = [bool]$subscribe.Checked
    plan      = if ($planB.Checked) { 'B' } else { 'A' }
  }
  $json = $payload | ConvertTo-Json -Compress
  [System.IO.File]::WriteAllText($Out, $json, (New-Object System.Text.UTF8Encoding($false)))
  $form.Text = 'LD Form (submitted)'
})
$form.Controls.Add($submit)

[void]$form.ShowDialog()
