"""Use installed Anaconda; isolate legacy requests environment handling to this process."""
import sys
import requests
original = requests.Session.__init__
def initialize(self,*args,**kwargs):
    original(self,*args,**kwargs)
    self.trust_env=False
requests.Session.__init__=initialize
target=sys.argv[1]
sys.argv=['conda','create','--prefix',target,'python=3.11','pip','-y']
from conda.cli import main
sys.exit(main())
