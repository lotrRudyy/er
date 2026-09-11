Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogPath = Join-Path $ScriptDir "stripe_fiscal_log.csv"

# --- Configuration ---
$PrinterIp = "192.168.8.201"
$PrinterUrl = "http://$PrinterIp/cgi-bin/fpmate.cgi"
$Department = 11
$ItemDescription = "ESCAPE ROOM"
$PaymentDescription = "STRIPE"

function Escape-Xml([string]$Text) {
    return [System.Security.SecurityElement]::Escape($Text)
}

function Invoke-EpsonXml([string]$BodyXml) {
    $soap = @"
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
$BodyXml
  </s:Body>
</s:Envelope>
"@
    try {
        $r = Invoke-WebRequest `
            -Uri $PrinterUrl `
            -Method Post `
            -ContentType "text/xml; charset=utf-8" `
            -Body $soap `
            -TimeoutSec 15 `
            -UseBasicParsing

        return [pscustomobject]@{
            Ok = $true
            HttpStatus = $r.StatusCode
            Content = [string]$r.Content
            Error = $null
        }
    }
    catch {
        return [pscustomobject]@{
            Ok = $false
            HttpStatus = $null
            Content = ""
            Error = $_.Exception.Message
        }
    }
}


function Reset-EpsonOpenDocument {
    $body = @'
    <printerCommand>
      <resetPrinter operator="1" />
    </printerCommand>
'@
    return Invoke-EpsonXml $body
}

function Test-Department11 {
    $body = @'
    <printerCommand>
      <directIO command="4202" data="11" />
    </printerCommand>
'@
    $result = Invoke-EpsonXml $body
    if (-not $result.Ok) {
        return [pscustomobject]@{ Ok=$false; Message=("Reparto 11 konnte nicht gelesen werden: " + $result.Error); Raw="" }
    }
    if ($result.Content -notmatch '<response\s+success="true"') {
        return [pscustomobject]@{ Ok=$false; Message="Reparto 11 wurde von der Epson nicht erfolgreich gelesen."; Raw=$result.Content }
    }
    if ($result.Content -notmatch '<responseData>11') {
        return [pscustomobject]@{ Ok=$false; Message="Reparto 11 wurde nicht wie erwartet erkannt."; Raw=$result.Content }
    }
    return [pscustomobject]@{ Ok=$true; Message="Reparto 11 erkannt (Standard N2 / non soggetta)."; Raw=$result.Content }
}

function Test-EpsonConnection {
    $body = @'
    <printerCommand>
      <queryPrinterStatus operator="1" statusType="1" />
    </printerCommand>
'@
    $result = Invoke-EpsonXml $body
    if (-not $result.Ok) {
        return [pscustomobject]@{ Ok=$false; Message=("Keine Verbindung zur Epson: " + $result.Error); Raw="" }
    }

    if ($result.Content -notmatch '<response\s+success="true"') {
        return [pscustomobject]@{ Ok=$false; Message="Epson antwortet, meldet aber keinen erfolgreichen Status."; Raw=$result.Content }
    }

    $training = ""
    $main = ""
    $sub = ""
    $files = ""
    if ($result.Content -match '<rtTrainingMode>(.*?)</rtTrainingMode>') { $training = $Matches[1] }
    if ($result.Content -match '<rtMainStatus>(.*?)</rtMainStatus>') { $main = $Matches[1] }
    if ($result.Content -match '<rtSubStatus>(.*?)</rtSubStatus>') { $sub = $Matches[1] }
    if ($result.Content -match '<rtFileToSend>(.*?)</rtFileToSend>') { $files = $Matches[1] }

    if ($training -and $training -ne "0") {
        return [pscustomobject]@{ Ok=$false; Message="ACHTUNG: Epson ist im Training Mode. Kein Beleg wird erstellt."; Raw=$result.Content }
    }

    $depCheck = Test-Department11
    if (-not $depCheck.Ok) {
        return [pscustomobject]@{ Ok=$false; Message=$depCheck.Message; Raw=$depCheck.Raw }
    }

    $msg = "Epson erreichbar. RT-Status: $main/$sub | Reparto 11 OK"
    if ($files) { $msg += " | Dateien zu senden: $files" }
    return [pscustomobject]@{ Ok=$true; Message=$msg; Raw=$result.Content }
}

function Format-EpsonAmount([decimal]$Amount) {
    return $Amount.ToString("0.00", [System.Globalization.CultureInfo]::GetCultureInfo("it-IT"))
}

function Get-DuplicateLogEntry([decimal]$Amount, [datetime]$PaymentDateTime) {
    if (-not (Test-Path $LogPath)) { return $null }
    try {
        $rows = Import-Csv -Path $LogPath
        $amountKey = $Amount.ToString("0.00", [System.Globalization.CultureInfo]::InvariantCulture)
        $dtKey = $PaymentDateTime.ToString("yyyy-MM-dd HH:mm")
        return $rows | Where-Object {
            $_.Status -eq "SUCCESS" -and
            $_.Amount -eq $amountKey -and
            $_.PaymentDateTime -eq $dtKey
        } | Select-Object -First 1
    }
    catch {
        return $null
    }
}

function Append-Log([string]$Status, [decimal]$Amount, [datetime]$PaymentDateTime, [string]$Response) {
    $cleanResponse = (($Response -replace "`r"," " -replace "`n"," ").Replace('"', "'"))
    $entry = [pscustomobject]@{
        CreatedAt = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        Status = $Status
        Amount = $Amount.ToString("0.00", [System.Globalization.CultureInfo]::InvariantCulture)
        PaymentDateTime = $PaymentDateTime.ToString("yyyy-MM-dd HH:mm")
        PrinterIp = $PrinterIp
        Department = $Department
        Response = $cleanResponse
    }
    if (Test-Path $LogPath) {
        $entry | Export-Csv -Path $LogPath -NoTypeInformation -Append -Encoding UTF8
    } else {
        $entry | Export-Csv -Path $LogPath -NoTypeInformation -Encoding UTF8
    }
}

function Create-StripeReceipt([decimal]$Amount, [datetime]$PaymentDateTime) {
    $amountIt = Format-EpsonAmount $Amount
    $dateText = $PaymentDateTime.ToString("dd.MM.yyyy")
    $timeText = $PaymentDateTime.ToString("HH:mm")
    $note = Escape-Xml ("STRIPE " + $dateText + " " + $timeText)
    $item = Escape-Xml $ItemDescription
    $payment = Escape-Xml $PaymentDescription

    # paymentType=2 + index=1 = electronic / credit-card totalizer 1.
    # No authorizeSales call is made, so this tool does not initiate a new Nexi card charge.
    $body = @"
    <printerFiscalReceipt>
      <Printer Num="1" />
      <beginFiscalReceipt operator="1" />
      <printRecItem operator="1" description="$item" quantity="1" unitPrice="$amountIt" department="$Department" justification="1" />
      <printRecMessage operator="1" messageType="4" message="$note" />
      <printRecTotal operator="1" description="$payment" payment="$amountIt" paymentType="2" index="1" justification="1" />
      <endFiscalReceipt operator="1" />
    </printerFiscalReceipt>
"@

    return Invoke-EpsonXml $body
}

# ---------------- UI ----------------

$form = New-Object System.Windows.Forms.Form
$form.Text = "Stripe -> Epson RT"
$form.Size = New-Object System.Drawing.Size(570, 520)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$title = New-Object System.Windows.Forms.Label
$title.Text = "STRIPE - Documento Commerciale"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 16, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(25, 20)
$form.Controls.Add($title)

$sub = New-Object System.Windows.Forms.Label
$sub.Text = "Epson RT: $PrinterIp   |   Reparto: $Department (N2 / non soggetta)   |   Zahlung: elektronisch / STRIPE"
$sub.AutoSize = $true
$sub.Location = New-Object System.Drawing.Point(28, 58)
$form.Controls.Add($sub)

$amountLabel = New-Object System.Windows.Forms.Label
$amountLabel.Text = "Betrag (EUR)"
$amountLabel.AutoSize = $true
$amountLabel.Location = New-Object System.Drawing.Point(30, 105)
$form.Controls.Add($amountLabel)

$amount = New-Object System.Windows.Forms.NumericUpDown
$amount.DecimalPlaces = 2
$amount.Minimum = 0.01
$amount.Maximum = 999999.99
$amount.Increment = 1
$amount.Value = 100
$amount.Font = New-Object System.Drawing.Font("Segoe UI", 14)
$amount.Size = New-Object System.Drawing.Size(180, 35)
$amount.Location = New-Object System.Drawing.Point(30, 128)
$form.Controls.Add($amount)

$dateLabel = New-Object System.Windows.Forms.Label
$dateLabel.Text = "Stripe-Zahlungsdatum"
$dateLabel.AutoSize = $true
$dateLabel.Location = New-Object System.Drawing.Point(30, 185)
$form.Controls.Add($dateLabel)

$date = New-Object System.Windows.Forms.DateTimePicker
$date.Format = [System.Windows.Forms.DateTimePickerFormat]::Custom
$date.CustomFormat = "dd.MM.yyyy"
$date.Value = Get-Date
$date.Size = New-Object System.Drawing.Size(180, 28)
$date.Location = New-Object System.Drawing.Point(30, 208)
$form.Controls.Add($date)

$timeLabel = New-Object System.Windows.Forms.Label
$timeLabel.Text = "Stripe-Zahlungszeit"
$timeLabel.AutoSize = $true
$timeLabel.Location = New-Object System.Drawing.Point(250, 185)
$form.Controls.Add($timeLabel)

$time = New-Object System.Windows.Forms.DateTimePicker
$time.Format = [System.Windows.Forms.DateTimePickerFormat]::Custom
$time.CustomFormat = "HH:mm"
$time.ShowUpDown = $true
$time.Value = Get-Date
$time.Size = New-Object System.Drawing.Size(120, 28)
$time.Location = New-Object System.Drawing.Point(250, 208)
$form.Controls.Add($time)

$info = New-Object System.Windows.Forms.Label
$info.Text = "Wichtig: Datum/Uhrzeit werden als Stripe-Zahlungszeit auf dem Beleg dokumentiert.`r`nDer fiskalische Beleg selbst wird mit dem tatsaechlichen Ausstellungszeitpunkt der Epson erzeugt."
$info.AutoSize = $false
$info.Size = New-Object System.Drawing.Size(500, 55)
$info.Location = New-Object System.Drawing.Point(30, 260)
$form.Controls.Add($info)

$testButton = New-Object System.Windows.Forms.Button
$testButton.Text = "Verbindung testen"
$testButton.Size = New-Object System.Drawing.Size(150, 38)
$testButton.Location = New-Object System.Drawing.Point(30, 325)
$form.Controls.Add($testButton)

$printButton = New-Object System.Windows.Forms.Button
$printButton.Text = "STRIPE-BELEG ERSTELLEN"
$printButton.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$printButton.Size = New-Object System.Drawing.Size(235, 38)
$printButton.Location = New-Object System.Drawing.Point(200, 325)
$form.Controls.Add($printButton)

$status = New-Object System.Windows.Forms.TextBox
$status.Multiline = $true
$status.ReadOnly = $true
$status.ScrollBars = "Vertical"
$status.Size = New-Object System.Drawing.Size(500, 75)
$status.Location = New-Object System.Drawing.Point(30, 390)
$status.Text = "Bereit. Zuerst 'Verbindung testen' verwenden."
$form.Controls.Add($status)

$testButton.Add_Click({
    $status.Text = "Pruefe Epson..."
    $form.Refresh()
    $r = Test-EpsonConnection
    $status.Text = $r.Message
})

$printButton.Add_Click({
    $paymentDateTime = Get-Date `
        -Year $date.Value.Year `
        -Month $date.Value.Month `
        -Day $date.Value.Day `
        -Hour $time.Value.Hour `
        -Minute $time.Value.Minute `
        -Second 0

    if ($paymentDateTime -gt (Get-Date).AddMinutes(5)) {
        [System.Windows.Forms.MessageBox]::Show(
            "Die Stripe-Zahlungszeit liegt in der Zukunft. Bitte pruefen.",
            "Ungueltige Zahlungszeit",
            "OK",
            "Warning"
        ) | Out-Null
        return
    }

    $connection = Test-EpsonConnection
    if (-not $connection.Ok) {
        $status.Text = $connection.Message
        [System.Windows.Forms.MessageBox]::Show(
            $connection.Message,
            "Epson nicht bereit",
            "OK",
            "Error"
        ) | Out-Null
        return
    }

    $dup = Get-DuplicateLogEntry -Amount $amount.Value -PaymentDateTime $paymentDateTime
    if ($dup) {
        $dupAnswer = [System.Windows.Forms.MessageBox]::Show(
            "Dieser Betrag mit exakt diesem Stripe-Datum und dieser Uhrzeit wurde laut lokalem Log bereits erfolgreich gebucht.`r`n`r`nTrotzdem NOCH EINEN Beleg erstellen?",
            "Moeglicher Doppelbeleg",
            "YesNo",
            "Warning"
        )
        if ($dupAnswer -ne "Yes") { return }
    }

    $summary = "Betrag: {0:N2} EUR`r`nStripe-Zahlung: {1}`r`nZahlungsart: elektronisch / STRIPE`r`nReparto: {2} (N2 / non soggetta)`r`n`r`nJetzt einen echten Documento Commerciale erstellen?" -f `
        $amount.Value, $paymentDateTime.ToString("dd.MM.yyyy HH:mm"), $Department

    $answer = [System.Windows.Forms.MessageBox]::Show(
        $summary,
        "Echten Beleg erstellen",
        "YesNo",
        "Warning"
    )
    if ($answer -ne "Yes") { return }

    $status.Text = "Beleg wird an Epson gesendet..."
    $form.Refresh()

    $result = Create-StripeReceipt -Amount $amount.Value -PaymentDateTime $paymentDateTime

    if (-not $result.Ok) {
        Append-Log -Status "ERROR" -Amount $amount.Value -PaymentDateTime $paymentDateTime -Response $result.Error
        $status.Text = "Fehler: " + $result.Error
        [System.Windows.Forms.MessageBox]::Show(
            "Keine erfolgreiche HTTP-Verbindung zur Epson.`r`n`r`n" + $result.Error,
            "Fehler",
            "OK",
            "Error"
        ) | Out-Null
        return
    }

    if ($result.Content -match '<response\s+success="true"') {
        Append-Log -Status "SUCCESS" -Amount $amount.Value -PaymentDateTime $paymentDateTime -Response $result.Content
        $status.Text = "ERFOLG: Epson hat den Befehl erfolgreich verarbeitet. Bitte gedruckten Beleg kurz kontrollieren."
        [System.Windows.Forms.MessageBox]::Show(
            "Epson meldet Erfolg. Bitte den gedruckten Documento Commerciale kontrollieren.",
            "Beleg erstellt",
            "OK",
            "Information"
        ) | Out-Null
    } else {
        $reset = Reset-EpsonOpenDocument
        $resetNote = ""
        if ($reset.Ok -and $reset.Content -match '<response\s+success="true"') {
            $resetNote = "`r`nOffener Epson/JavaPOS-Beleg wurde automatisch zurueckgesetzt."
        } else {
            $resetNote = "`r`nAutomatisches Reset konnte nicht bestaetigt werden. Druckerstatus pruefen."
        }

        Append-Log -Status "PRINTER_ERROR" -Amount $amount.Value -PaymentDateTime $paymentDateTime -Response ($result.Content + " RESET=" + $reset.Content)
        $status.Text = "Epson-Fehler." + $resetNote + "`r`n" + $result.Content
        [System.Windows.Forms.MessageBox]::Show(
            "Die Epson hat keinen Erfolg gemeldet." + $resetNote + "`r`n`r`nNicht erneut klicken, bevor der Fehler geklaert ist.",
            "Epson-Fehler",
            "OK",
            "Error"
        ) | Out-Null
    }
})

[void]$form.ShowDialog()
