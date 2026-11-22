// Drive the local packaged app through its ordinary model picker and composer.
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')
const { _electron } = require('../../apps/desktop/node_modules/@playwright/test')
const models = {
  sol: ['gpt-6.2-sol', /^GPT-6\.2-sol(?:Med|Low|High)?$/i],
  opus: ['claude-opus-5-5[1m]', /^Opus 5\.5\s*1M(?:Med|Low|High)?$/i],
}

async function run() {
  const tasks = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
  const app = await _electron.launch({
    executablePath: path.join(os.homedir(), 'Applications/Theseus.app/Contents/MacOS/Theseus'),
    timeout: 30000,
  })
  try {
    const page = await app.firstWindow()
    page.setDefaultTimeout(60000)
    for (const {model, prompt} of tasks) {
      const [id, label] = models[model]
      await page.locator('[data-tour="sidebar-nav-new-session"]').click()
      const picker = page.locator('button[aria-label^="Model ·"]:visible').last()
      if (!(await picker.getAttribute('aria-label')).includes(id)) {
        await picker.click()
        await page.getByRole('textbox', {name:'Search models'}).fill(id)
        await page.locator('[role="menuitem"]').filter({hasText:label}).first().click({position:{x:80,y:10}})
        for (let i=0;i<3 && await page.locator('[role="menu"]:visible').count();i++) await page.keyboard.press('Escape')
      }
      await page.waitForFunction(id => [...document.querySelectorAll('button[aria-label^="Model ·"]')].some(b=>b.getClientRects().length && b.getAttribute('aria-label').includes(id)), id)
      await page.locator('[contenteditable="true"][aria-label="Message"]:visible').last().fill(prompt)
      await page.locator('button[aria-label="Send"]:visible').last().click()
      const phase=page.locator('.theseus-ambient-backdrop').first()
      await page.waitForFunction(()=>document.querySelector('.theseus-ambient-backdrop')?.dataset.phase!=='Idle',undefined,{timeout:30000})
      await phase.locator('xpath=self::*[@data-phase="Idle"]').waitFor({timeout:180000})
      console.log('Finished',model)
    }
  } finally { await app.close() }
}
run().catch(e=>{console.error(e.message);process.exitCode=1})
