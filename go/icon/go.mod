module apollo/icon

go 1.26.0

require (
	apollo v0.0.0
	github.com/tc-hib/winres v0.2.1
)

require (
	github.com/nfnt/resize v0.0.0-20180221191011-83c6a9932646 // indirect
	golang.org/x/image v0.12.0 // indirect
)

// the icon's drawing lives in the app (../appicon), read through this replace
replace apollo v0.0.0 => ..
