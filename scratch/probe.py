import sys; sys.path.insert(0,'..')
from atlas.fetch import fetch
import re
urls="""https://www.aicte-india.org/schemes/students-development-schemes
https://www.reliancefoundation.org/education
https://www.scholarships.reliancefoundation.org/
https://www.tatatrusts.org/our-work/individual-grants-programmes/education-grants
https://www.jntataendowment.org/
https://www.inlaksfoundation.org/
https://www.chevening.org/scholarship/india/
https://www.usief.org.in/Fulbright-Nehru-Fellowships.aspx
https://www.education.gov.in/scholarships
https://www.ugc.gov.in/page/Scholarships.aspx
https://online-inspire.gov.in/
https://minorityaffairs.gov.in/WriteReadData/RTF1984/
https://tribal.nic.in/
https://kcmet.org/
https://www.nsfoundation.co.in/
https://www.hdfcbank.com/personal/about-us/corporate-social-responsibility/hdfc-bank-parivartans-ecss-programme
https://www.adityabirla.com/corporate-social-responsibility/scholarships
https://www.iitb.ac.in/en/education/scholarships
https://admissions.ashoka.edu.in/financial-aid
https://www.bits-pilani.ac.in/pilani/scholarships/
https://www.iisc.ac.in/
https://www.buddy4study.com/scholarships
https://www.scholarships.net.in/
https://www.cdn.cmmaid.in
https://www.sitaramjindalfoundation.org/scholarships.php
https://www.pmsss.mhrd.gov.in/
https://www.commonwealthscholarships.org
https://www.daad.in/en/
https://www.britishcouncil.in/study-uk/scholarships
https://www.nsdl.co.in
https://ssp.postmatric.karnataka.gov.in/
https://www.sbifoundation.in/focus-area/education
https://www.hindalco.com
https://www.wipro.com/
https://www.mahindra.com/sustainability/education""".split()
for u in urls:
    d=fetch(u,delay=0.3)
    t=d.get('text','')
    print(d.get('status'),len(t),len(re.findall('(?i)scholarship',t)),u, d.get('error',''))
